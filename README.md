# django-partial-state

## Summary

This library extends the Django ORM to allow for shadow tables with partially built objects.
The use case is more or less the following: suppose that you have some model with fields that&mdash;for the purposes of the business logic in your application&mdash;should not be nullable.
However, for whatever practical reason (e.g. a multipart form that needs to persist data between requests), not all of the data necessary to populate these non-nullable fields are available on object creation.

This library allows you to

 - persist objects with an incomplete state, and
 - preserve all non-null constraints on "finished" objects.

To achieve this, the library creates a shadow table with the same fields as the original one, but with all columns marked as nullable.
You can then build up the object step by step, and move it into the main table when all necessary fields are populated.
If necessary, you can add your own validation logic to the state table, or even specify additional temporary fields.

## Quickstart

Here's a minimal example.

```python
from django.db import models
from partial_state import PartialStateRecord

class Example(models.Model):

    number_column = models.IntegerField()
    text_column = models.CharField(max_length=10)

    partial = PartialStateRecord()
```

Run `makemigrations` and `migrate`. You can then do
```
>>> shadow = Example(text_column='abcde').partial
>>> shadow.save()
>>> shadow.pk
1
```
to save a partial `Example` object to the database.
You can pick up where you left off by running
```
>>> shadow = Example.partial.by_partial_state_id(1)
>>> shadow.number_column = 5
>>> shadow.shelve()
```
The call to `shelve()` saves the object in the "real" `Example` table, and deletes the temporary shadow copy.

## Documentation

For now, please refer to the example code under `tests` (or read the source, of course!).
More detailed docs may or may not be made available in the future.

## Known limitations

 - You are responsible for implementing any uniqueness checks that take the shadow objects into account, if your use case requires it. Future iterations might offer more opinionated solutions to deal with this issue.
 - Many-to-many relations are not supported.
 - The behaviour of foreign keys and multi-table inheritance relationships comes with a few gotchas, and this library does not make any serious attempt to replicate all of Django's ORM magic concerning foreign keys. Hence, if you want to try anything complicated, you might be better off implementing your own problem-specific state wrangling solution.
 - This project was born out of a curiosity-driven afternoon hacking session. It includes a few rudimentary tests, but it shouldn't be considered production-ready. 
   That said, pull requests with improvements are welcome, I'll try to get to them in a timely manner. :)