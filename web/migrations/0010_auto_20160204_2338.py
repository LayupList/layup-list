# -*- coding: utf-8 -*-
# Generated by Django 1.9.2 on 2016-02-04 23:38
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('web', '0009_auto_20160203_0424'),
    ]

    operations = [
        migrations.AlterField(
            model_name='review',
            name='term',
            field=models.CharField(db_index=True, max_length=3),
        ),
    ]