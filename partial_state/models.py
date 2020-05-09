import copy
from datetime import timedelta

from django.db import models, transaction

# Field bookkeeping code loosely based on the shadow model trick in the
#  Pro Django book, and https://github.com/treyhunner/django-simple-history
from django.utils import timezone
from django.utils.deconstruct import deconstructible

from partial_state import manager

__all__ = ['PartialStateMixin', 'PartialStateRecord']


class PartialStateMixin:

    def post_shelve_cleanup(self):
        """
        Called from within the shelving transaction, so errors will
        abort the entire thing
        :return:
        """
        # noinspection PyUnresolvedReferences
        self.delete()

    def wrap(self, populate_relations=True):
        # noinspection PyUnresolvedReferences
        model = self.wrapped_model

        def get_values():
            for f in model._meta.get_fields(include_parents=False):
                if isinstance(f, models.AutoField):
                    continue
                # this is a bit of a hack, but in the grand scheme of things,
                #  it's relatively clean
                attr = getattr(self, f.attname)
                if populate_relations and f.is_relation:
                    remote_model = f.remote_field.model
                    rel_value = remote_model._base_manager.get(pk=attr)
                    yield f.name, rel_value
                    # note that the above does NOT work with the special magic
                    # in parent/child relationships in multi-table inheritance:
                    # the attributes on the parent are not propagated to the
                    # child automatically when objects are initialised in this
                    # manner.
                else:
                    yield f.attname, attr

        values = dict(get_values())
        obj = model(**values)

        return obj

    def shelve(self):
        wrapped_obj = self.wrap(populate_relations=False)
        # call validation logic on model
        wrapped_obj.clean()

        # noinspection PyUnresolvedReferences
        meta = self.wrapped_model._meta

        with transaction.atomic():
            # this damn well should error if the PK is taken,
            # so pass force_insert
            if len(meta.parents) > 0:
                # Force a raw save to avoid messing up possible parent
                # objects. Not ideal, but there's not much we can do until
                # https://code.djangoproject.com/ticket/7623 gets fixed.
                wrapped_obj.save_base(force_insert=True, raw=True)
            else:
                wrapped_obj.save(force_insert=True)
            self.post_shelve_cleanup()

        return wrapped_obj


@deconstructible
class ExpiryDefault:

    def __init__(self, lifetime):
        self.lifetime = lifetime

    def __call__(self):
        return timezone.now() + self.state_lifetime


class PartialStateRecord:

    def __init__(self, state_lifetime=None, db_table_format='%s_partialstate',
                 model_name_format='%sPartialState',
                 mixin_base=PartialStateMixin,
                 manager_factory=manager.PartialObjectManager):
        self.state_lifetime: timedelta = state_lifetime
        self.db_table_format = db_table_format
        self.model_name_format = model_name_format
        self.mixin_base = mixin_base
        self.partial_descriptor_name = None
        self.manager_factory = manager_factory

    def contribute_to_class(self, cls, name):
        self.partial_descriptor_name = name
        models.signals.class_prepared.connect(self.finalize, sender=cls)

    def finalize(self, sender, **_kwargs):
        state_model = self.create_state_model(sender)

        partial_wrapper = manager.PartialObjectDescriptor(
            state_model, self.manager_factory
        )
        setattr(sender, self.partial_descriptor_name, partial_wrapper)

    def create_state_model(self, model):
        attrs = {
            field.name: field for field in self.copy_fields(model)
        }
        attrs['__module__'] = model.__module__
        attrs['wrapped_model'] = model
        attrs['_state_expires'] = self.state_lifetime is not None
        attrs.update(self.state_model_extra_fields(model))
        attrs.update(
            Meta=type('Meta', (), self.state_model_meta_options(model))
        )
        name = self.model_name_format % model._meta.object_name
        # and let the metaclass work its magic
        # TODO make an attempt to copy methods off the model we're cloning
        return type(name, (models.Model, self.mixin_base), attrs)

    def copy_fields(self, model):

        # TODO allow for smart handling of foreign keys
        #  between models that support partial data?
        # can't use get_fields yet, so we have to use Django's private API
        for field in model._meta.local_concrete_fields:

            # TODO Figure out a good way to handle these
            if isinstance(field, models.ManyToManyField):
                raise ValueError(
                    "ManyToManyFields are not supported."
                )

            # there's no point in keeping these around
            if isinstance(field, models.AutoField):
                continue

            field = copy.copy(field)
            # we attempt to preserve the primary key field, since it
            #  might have some semantic value if it's not an AutoField
            # TODO deal with expiring state & uniqueness somehow
            #  (for now, this is handled using latest() in the manager)
            if field.primary_key:
                field.primary_key = False
            else:
                field.null = True

            if isinstance(field, models.ForeignKey):
                # recreate the field, shallow copies don't work well with FKs
                # TODO leaving on_delete as-is seems to be the most sensible
                #   here, but allowing it to be customised isn't too
                #  unreasonable

                # calling deconstruct() before the app registry is done
                #  trips up the logic around swappable fields, so we have
                #  to trick the model loader
                swappable = field.swappable
                field.swappable = False
                try:
                    name, path, args, kwargs = field.deconstruct()
                finally:
                    field.swappable = swappable

                ftype = type(field)
                # convert to OneToOneField to ForeignKey
                if isinstance(field, models.OneToOneField):
                    ftype = models.ForeignKey

                # we explicitly don't want backreferences to objects with
                #  broken state
                # Multi-table inheritance logic is also explicitly disabled
                #  for now.
                # TODO do something about that
                kwargs.update(
                    related_name='+', serialize=True, auto_created=False,
                    parent_link=False
                )

                # self binds should probably point to the "mother" table,
                # since they presumably refer to objects that already exist
                try:
                    if kwargs['to'] == 'self':
                        kwargs['to'] = field.model
                except KeyError:
                    # TODO anticipate issues with subclasses of ForeignKey that
                    #  set to= in some other way
                    pass

                field = ftype(*args, **kwargs)
                field.name = name
            elif field.unique:
                # replace unique marker with non-unique index
                field.unique = False
                field.db_index = True

            yield field

    # noinspection PyUnusedLocal
    def state_model_extra_fields(self, model):
        # Client classes can override this method to provide their own state
        #  logic, such as keeping track of "stages" in which an object is
        #  being built up, etc.

        fields = {
            'partial_state_id': models.AutoField(primary_key=True),
        }

        if self.state_lifetime is not None:
            # more complicated expiry timestamp logic can always be
            # implemented through the clean() method
            fields['partial_state_expiry'] = models.DateTimeField(
                default=ExpiryDefault(self.state_lifetime),
            )
        return fields

    # TODO make an effort to inherit more complex meta options
    def state_model_meta_options(self, model):
        return {
            'db_table': self.db_table_format % model._meta.db_table,
            'ordering': ('-partial_state_id',),
            'get_latest_by': 'partial_state_id',
        }
