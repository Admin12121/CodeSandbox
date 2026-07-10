from nexorm.migrations.operations import *

# sandbox_template_plans.template_id was created as INTEGER by migrations
# 0010/0011 because the migration writer nests ForeignKey target_field info
# under a "foreign_key" sub-dict, while the dialect's column-type resolver
# only checked for it directly on the field — so it silently fell back to
# the generic ForeignKey->INTEGER mapping instead of following SandboxTemplate
# (a string/UUID primary key). Fixed in nexorm's dialects/base.py; this
# migration corrects the already-applied column.
operations = [
    AlterColumn(
        table='sandbox_template_plans',
        old_column={'name': 'template_id', 'type': 'ForeignKey', 'nullable': False, 'unique': False,
                    'default': None, 'primary_key': False, 'index': True, 'auto_increment': False,
                    'max_length': None, 'max_digits': None, 'decimal_places': None,
                    'foreign_key': {'to': 'SandboxTemplate', 'on_delete': 'CASCADE', 'related_name': None,
                                    'target_field': {'type': 'StringField', 'primary_key': True, 'nullable': False,
                                                      'unique': False, 'default': None, 'index': False,
                                                      'auto_increment': False, 'max_length': 36}}},
        new_column={'name': 'template_id', 'type': 'ForeignKey', 'nullable': False, 'unique': False,
                    'default': None, 'primary_key': False, 'index': True, 'auto_increment': False,
                    'max_length': None, 'max_digits': None, 'decimal_places': None,
                    'foreign_key': {'to': 'SandboxTemplate', 'on_delete': 'CASCADE', 'related_name': None,
                                    'target_field': {'type': 'StringField', 'primary_key': True, 'nullable': False,
                                                      'unique': False, 'default': None, 'index': False,
                                                      'auto_increment': False, 'max_length': 36}}},
    ),
]
