from django.db import models
from django.db.models.functions import Now

__all__ = ['PartialObjectManager']


class PartialObjectDescriptor:
    def __init__(self, state_model, manager_factory):
        self.state_model = state_model
        self.manager_factory = manager_factory

    def __get__(self, instance, owner):
        # when not None, instance is an object of the model being wrapped

        if instance is not None:
            model = self.state_model.wrapped_model
            values = {
                f.attname: getattr(instance, f.attname)
                for f in model._meta.local_concrete_fields
                if not isinstance(f, models.AutoField)
            }
            return self.state_model(**values)

        return self.manager_factory(self.state_model)


class PartialObjectManager(models.Manager):

    def __init__(self, state_model):
        super().__init__()
        self.model = state_model
        self.wrapped_model = state_model.wrapped_model

    def get_queryset(self):
        base_qs = super().get_queryset()
        if not self.model._state_expires:
            return base_qs

        # only fetch non-expired instances
        # using the transaction TS would be better for reproducibility,
        #  but only Postgres supports that
        return base_qs.filter(partial_state_expiry__gte=Now())

    def purge_expired(self):
        if not self.model._state_expires:
            raise TypeError('State model does not use expiry timestamps.')
        qs = super().get_queryset().filter(partial_state_expiry__lt=Now())
        return qs.delete()

    def by_partial_state_id(self, state_id):
        """
        Fetch a partial object by it's temporary state ID.

        :param state_id:
        :return:
        """
        return self.get_queryset().get(partial_state_id=state_id)

    def by_true_pk(self, pk):
        """
        Fetch a partial object by it's would-be true ID in the permanent table.
        By design, this does NOT work for AutoFields.

        :param pk:
        :return:
        """

        qs_filter = {self.wrapped_model._meta.pk.attname: pk}
        return self.get_queryset().filter(**qs_filter).latest()
