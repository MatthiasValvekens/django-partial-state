from django.db import IntegrityError
from django.test import TestCase
from . import models


class TestSimpleScenario(TestCase):

    def test_create_fail(self):
        obj = models.TestA(column2='abcde')
        with self.assertRaises(IntegrityError):
            obj.save()

    def test_simple_create(self):
        obj = models.TestA(column2='abcde')
        partial_obj = obj.partial
        partial_obj.save()
        partial_pk = partial_obj.pk
        partial_obj = models.TestA.partial.by_partial_state_id(partial_pk)
        self.assertEqual(partial_obj.column2, 'abcde')
        self.assertIsNone(partial_obj.column1)

        partial_obj.column1 = 5
        obj = partial_obj.shelve()
        obj = models.TestA.objects.get(pk=obj.pk)
        self.assertEqual(obj.column1, 5)
        self.assertEqual(obj.column2, 'abcde')
        self.assertFalse(models.TestA.partial.filter(pk=partial_pk).exists())


class TestInheritanceScenario(TestCase):

    @classmethod
    def setUpTestData(cls):
        user = models.User(email='abc@example.com', somenumber=5)
        user.save()
        cls.example_user = user

    def test_create_fail(self):
        profile = models.Profile(username='abc', user_ptr=self.example_user)
        with self.assertRaises(IntegrityError):
            profile.save()

    def test_parent_access(self):
        profile = models.Profile(username='abc', user_ptr=self.example_user)
        partial_obj = profile.partial
        partial_obj.save()
        partial_pk = partial_obj.pk
        partial_obj = models.Profile.partial.by_partial_state_id(partial_pk)

        # check if we can still access the User attributes through
        #  wrapped_object
        self.assertEqual(partial_obj.user_ptr_id, self.example_user.pk)
        self.assertEqual(partial_obj.user_ptr, self.example_user)
        wrapped = partial_obj.wrap()
        self.assertEqual(wrapped.user_ptr.pk, self.example_user.pk)
        # wrapped.email does not work as things are now
        self.assertEqual(wrapped.user_ptr.email, 'abc@example.com')

    def test_simple_create(self):
        profile = models.Profile(username='abc', user_ptr=self.example_user)
        partial_obj = profile.partial
        partial_obj.save()
        partial_pk = partial_obj.pk
        partial_obj = models.Profile.partial.by_partial_state_id(partial_pk)

        partial_obj.street_address = '5 ABC St.'
        partial_obj.postal_code = 20312
        obj = partial_obj.shelve()
        obj = models.Profile.objects.get(pk=obj.pk)
        self.assertEqual(obj.postal_code, 20312)
        self.assertFalse(models.Profile.partial.filter(pk=partial_pk).exists())

        self.assertEqual('abc@example.com', obj.email)
