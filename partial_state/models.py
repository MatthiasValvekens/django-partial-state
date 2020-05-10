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
    """Mixin class for partial state models.

    Implements extra functionality on the autogenerated models
    that handle incomplete objects.
    May be extended if necessary.
    """

    def post_shelve_cleanup(self):
        """Clean up after shelving an object.

        Called from within the shelving transaction, so errors will
        abort the entire thing.
        By default, this method just calls :func:`models.Model.delete` on
        `self`.
        """
        # noinspection PyUnresolvedReferences
        self.delete()

    def wrap(self, populate_relations=False):
        """Wrap a partial object.

        Wrap the (incomplete) data in this partial model instance into
        an instance of the original model class.
        This method is also called in :meth:`shelve`.

        :param populate_relations:
            Attempt to populate foreign key descriptors if True.
            This generates extra database queries, and is kind of a hack.
        :return:
            An instance of the original class, with whatever attributes that
            have been set so far.
        """
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
        """Shelve an object.

        Build an object to save in the finished object table by calling
        :meth:`wrap`, commit it to the database and call
        :meth:`post_shelve_cleanup`.

        :return:
            The permanent copy of the object that was just saved.
        """
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
    """Bookkeeping class for partial state models.

    Instances of this class are ephemeral and only exist during Django's
    model preparation phase.
    The attribute occupied by this object will be replaced by an instance of
    :class:`manager.PartialObjectDescriptor` afterwards.
    """

    def __init__(self, state_lifetime=None, db_table=None,
                 model_name=None, mixin_base=PartialStateMixin,
                 manager_factory=manager.PartialObjectManager):
        """High-level configuration for partial state records.

        :param state_lifetime:
            :class:`datetime.timedelta` object to describe how long a partial
            object should be considered "active" after being created. If `None`,
            then no lifetime metadata will be tracked.
            We provide a helper to clean up expired objects, but making sure
            it gets invoked is still your responsibility
            (see :class:`manager.PartialObjectManager.purge_expired`).
        :param db_table:
            Table name of the partial state table.
        :param model_name:
            Class name of the partial state model.
        :param mixin_base:
            Mixin class from which the partial state model will inherit, in
            addition to :class:`models.Model`.
        :param manager_factory:
            Manager class/factory that will be called with the partial state
            model as its argument to produce a manager to query the partial
            state table.
        """
        self.state_lifetime: timedelta = state_lifetime
        self.db_table = db_table
        self.state_model_name = model_name
        self.mixin_base = mixin_base
        self.partial_descriptor_name = None
        self.manager_factory = manager_factory

    def contribute_to_class(self, cls, name):
        self.partial_descriptor_name = name
        models.signals.class_prepared.connect(self.finalize, sender=cls)

    def finalize(self, sender, **_kwargs):
        """Execute partial state model creation logic.

        Handles both the model creation & manager setup.
        This runs as soon as the underlying class is prepared.

        :param sender:
            Model class to wrap.
        """
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
        name = self.state_model_name or (
                model._meta.object_name + 'PartialState'
        )
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
        """Extra fields to add to the partial state model.

        This method returns a dictionary with the fields that
        should be added to the partial state model in addition to the fields
        cloned from the original.
        By default, this dictionary includes the `partial_state_id` field, and
        the `partial_state_expiry` field (if requested).

        You can implement more complex state wrangling logic on top of what
        this package offers by adding your own extra fields, e.g. keeping track
        of the "stage" of the build process a partial object currently is in.

        :param model:
            The underlying model that's being cloned.
        :return:
            Dictionary of name-field pairs.
        """

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
        """Determine the meta attributes of the partial state model.

        The result of this vector is passed to the type constructor of the
        partial state model's `Meta` class.
        By default, this method only sets
            - the `db_table` attribute based on the value passed to the
              constructor, or by appending `_partialstate` to the `db_table`
              attribute of the original table.
            - `ordering` and `get_latest_by`, to sort by `partial_state_id`

        At the moment, no attempt is made to inherit more involved meta
        attributes from the original table, that's something you would have
        to take care of yourself.

        :param model:
            The model that's being cloned.
        :return:
            Dictionary with meta attribute values.
        """
        return {
            'db_table': self.db_table or (
                model._meta.db_table + '_partialstate'
            ),
            'ordering': ('-partial_state_id',),
            'get_latest_by': 'partial_state_id',
        }
