from nexorm.migrations.operations import *

operations = [
    AddColumn(table='user_passkeys', column={'name': 'is_enabled', 'field': {'type': 'BooleanField', 'primary_key': False, 'nullable': False, 'unique': False, 'default': True, 'index': False, 'auto_increment': False, 'max_length': None}, 'type': 'BooleanField', 'nullable': False, 'unique': False, 'default': True, 'primary_key': False, 'index': False, 'auto_increment': False, 'max_length': None, 'max_digits': None, 'decimal_places': None, 'foreign_key': None}),
]
