from datetime import timedelta

from django.db import models
from partial_state import PartialStateRecord


class TestA(models.Model):

    column1 = models.IntegerField()
    column2 = models.CharField(max_length=10)

    partial = PartialStateRecord()


class TestB(models.Model):

    column1 = models.IntegerField()
    column2 = models.CharField(max_length=10)

    partial = PartialStateRecord(state_lifetime=timedelta(days=3))


class User(models.Model):
    email = models.EmailField(max_length=250)
    somenumber = models.IntegerField()


# scenario: every user eventually needs an address, but for some reason
#  this information is not available at the beginning of the signup process
class Profile(User):
    username = models.CharField(max_length=50)
    # note that default=None is perfectly acceptable now
    street_address = models.CharField(blank=False, default=None, max_length=250)
    postal_code = models.IntegerField(null=False)

    partial = PartialStateRecord()
