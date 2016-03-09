# coding: utf-8

# $Id: $
import datetime

from django.core.exceptions import ImproperlyConfigured
import sys
from django.db.backends.creation import BaseDatabaseCreation
from django.utils import timezone
from django.utils.six import text_type, binary_type

try:
    import vertica_python as Database
except ImportError:
    e = sys.exc_info()[1]
    raise ImproperlyConfigured("Error loading vertica_python module: %s" % e)

from django.db import utils
from django.db.backends import BaseDatabaseWrapper, BaseDatabaseFeatures, BaseDatabaseValidation, \
    BaseDatabaseOperations, BaseDatabaseClient, BaseDatabaseIntrospection
from django.conf import settings
from django import VERSION

if VERSION >= (1, 7, 0):
    from django.db.backends.schema import BaseDatabaseSchemaEditor

    class DatabaseSchemaEditor(BaseDatabaseSchemaEditor):
        pass

# force errors in top-level module for vertica_python>=0.5
for e in ('DataError', 'OperationalError', 'IntegrityError', 'InternalError',
          'ProgrammingError', 'NotSupportedError', 'DatabaseError',
          'InterfaceError', 'Error'):
    attr = getattr(Database.errors, e)
    setattr(Database, e, attr)


class DatabaseCreation(BaseDatabaseCreation):
    data_types = {
        'AutoField': 'identity',
        'BinaryField': 'longblob',
        'BooleanField': 'bool',
        'CharField': 'varchar(%(max_length)s)',
        'CommaSeparatedIntegerField': 'varchar(%(max_length)s)',
        'DateField': 'date',
        'DateTimeField': 'datetime',
        'DecimalField': 'numeric(%(max_digits)s, %(decimal_places)s)',
        'FileField': 'varchar(%(max_length)s)',
        'FilePathField': 'varchar(%(max_length)s)',
        'FloatField': 'double precision',
        'IntegerField': 'integer',
        'BigIntegerField': 'bigint',
        'IPAddressField': 'char(15)',
        'GenericIPAddressField': 'char(39)',
        'NullBooleanField': 'bool',
        'OneToOneField': 'integer',
        'PositiveIntegerField': 'integer',
        'PositiveSmallIntegerField': 'smallint',
        'SlugField': 'varchar(%(max_length)s)',
        'SmallIntegerField': 'smallint',
        'TextField': 'longtext',
        'TimeField': 'time',
    }


DatabaseError = Database.DatabaseError
IntegrityError = Database.IntegrityError


class DatabaseFeatures(BaseDatabaseFeatures):
    pass


class DatabaseOperations(BaseDatabaseOperations):
    compiler_module = "vertica.compiler"

    def max_name_length(self):
        # :see https://my.vertica.com/docs/4.1/HTML/Master/10538.htm
        return 128

    def quote_name(self, name):
        if name.startswith('"') and name.endswith('"'):
            return name  # Quoting once is enough.
        return '"%s"' % name

    def last_insert_id(self, cursor, table_name, pk_name):
        cursor.execute("SELECT currval('%s_seq')" % table_name)
        return cursor.fetchone()[0]

    def validate_constraints(self, cursor, table_name):
        cursor.execute("SELECT ANALYZE_CONSTRAINTS(%s)", [table_name])
        if cursor.rowcount > 0:
            raise utils.IntegrityError('Constraints failed',
                                       {"row_count": cursor.rowcount,
                                        "first_row": cursor.fetchone()})


class DatabaseClient(BaseDatabaseClient):
    pass


class DatabaseValidation(BaseDatabaseValidation):
    pass


class DatabaseIntrospection(BaseDatabaseIntrospection):
    def get_table_list(self, cursor):
        "Returns a list of table names in the current database."
        query = ("SELECT table_name FROM v_catalog.tables")
        cursor.execute(query)
        return [row[0] for row in cursor.fetchall()]


class CursorWrapper(object):
    """
    A wrapper around the pyodbc's cursor that takes in account a) some pyodbc
    DB-API 2.0 implementation and b) some common ODBC driver particularities.
    """
    def __init__(self, cursor, encoding=""):
        self.cursor = cursor
        self.driver_supports_utf8 = True
        self.last_sql = ''
        self.last_params = ()
        self.encoding = encoding

    def format_sql(self, sql, n_params=None):
        if not self.driver_supports_utf8 and isinstance(sql, text_type):
            # Older FreeTDS (and other ODBC drivers?) don't support Unicode yet, so
            # we need to encode the SQL clause itself in utf-8
            sql = sql.encode('utf-8')
        return sql

    def format_params(self, params):
        fp = []
        for p in params:
            if isinstance(p, text_type):
                if not self.driver_supports_utf8:
                    # Older FreeTDS (and other ODBC drivers?) doesn't support Unicode
                    # yet, so we need to encode parameters in utf-8
                    fp.append(p.encode('utf-8'))
                else:
                    fp.append(p)
            elif isinstance(p, binary_type):
                if not self.driver_supports_utf8:
                    fp.append(p.decode(self.encoding).encode('utf-8'))
                else:
                    fp.append(p)
            elif isinstance(p, type(True)):
                if p:
                    fp.append(1)
                else:
                    fp.append(0)
            else:
                fp.append(p)
        return tuple(fp)

    def execute(self, sql, params=()):
        self.last_sql = sql
        sql = self.format_sql(sql, len(params))
        params = self.format_params(params)
        self.last_params = params
        try:
            return self.cursor.execute(sql, params)
        except IntegrityError:
            e = sys.exc_info()[1]
            raise utils.IntegrityError(*e.args)
        except DatabaseError:
            e = sys.exc_info()[1]
            raise utils.DatabaseError(*e.args)

    def executemany(self, sql, params_list):
        sql = self.format_sql(sql)
        # pyodbc's cursor.executemany() doesn't support an empty param_list
        if not params_list:
            if '?' in sql:
                return
        else:
            raw_pll = params_list
            params_list = [self.format_params(p) for p in raw_pll]

        try:
            return self.cursor.executemany(sql, params_list)
        except IntegrityError:
            e = sys.exc_info()[1]
            raise utils.IntegrityError(*e.args)
        except DatabaseError:
            e = sys.exc_info()[1]
            raise utils.DatabaseError(*e.args)

    def format_results(self, rows):
        """
        Decode data coming from the database if needed and convert rows to tuples
        (pyodbc Rows are not sliceable).
        """
        needs_utc = VERSION>= (1, 4, 0, 0) and settings.USE_TZ
        if not needs_utc:
            return tuple(rows)

        fr = []
        for row in rows:
            if needs_utc and isinstance(row, datetime.datetime):
                row = row.replace(tzinfo=timezone.utc)
            fr.append(row)
        return tuple(fr)

    def fetchone(self):
        row = self.cursor.fetchone()
        if row is not None:
            return self.format_results(row)
        return []

    def fetchmany(self, chunk):
        return [self.format_results(row) for row in self.cursor.fetchmany(chunk)]

    def fetchall(self):
        return [self.format_results(row) for row in self.cursor.fetchall()]

    def __getattr__(self, attr):
        if attr in self.__dict__:
            return self.__dict__[attr]
        return getattr(self.cursor, attr)

    def __iter__(self):
        return iter(self.cursor)


class DatabaseWrapper(BaseDatabaseWrapper):
    Database = Database

    operators = {
        'exact': '= %s',
        'iexact': 'ILIKE %s',
        'contains': 'LIKE %s',
        'icontains': 'ILIKE %s',
        'regex': 'REGEXP BINARY %s',
        'iregex': 'REGEXP %s',
        'gt': '> %s',
        'gte': '>= %s',
        'lt': '< %s',
        'lte': '<= %s',
        'startswith': 'LIKE %s',
        'endswith': 'LIKE %s',
        'istartswith': 'ILIKE %s',
        'iendswith': 'ILIKE %s',
    }

    def __init__(self, *args, **kwargs):
        super(DatabaseWrapper, self).__init__(*args, **kwargs)

        self.features = DatabaseFeatures(self)
        self.ops = DatabaseOperations(self)
        self.client = DatabaseClient(self)
        self.creation = DatabaseCreation(self)
        self.introspection = DatabaseIntrospection(self)
        self.validation = DatabaseValidation(self)

    def get_connection_params(self):
        settings_dict = self.settings_dict
        if not settings_dict['NAME']:
            from django.core.exceptions import ImproperlyConfigured
            raise ImproperlyConfigured(
                "settings.DATABASES is improperly configured. "
                "Please supply the NAME value.")
        conn_params = {
            'database': settings_dict['NAME'],
        }
        conn_params.update(settings_dict['OPTIONS'])
        if 'autocommit' in conn_params:
            del conn_params['autocommit']
        if settings_dict['USER']:
            conn_params['user'] = settings_dict['USER']
        if settings_dict['PASSWORD']:
            conn_params['password'] = settings_dict['PASSWORD']
        if settings_dict['HOST']:
            conn_params['host'] = settings_dict['HOST']
        if settings_dict['PORT']:
            conn_params['port'] = settings_dict['PORT']
        return conn_params

    def get_new_connection(self, conn_params):
        return Database.connect(**conn_params)

    def _set_autocommit(self, autocommit):
        mode = "ON" if autocommit else "OFF"
        with self.wrap_database_errors:
            cursor = self.connection.cursor()
            cursor.execute("SET SESSION AUTOCOMMIT TO %s" % mode)

    def create_cursor(self):
        cursor = self.connection.cursor()
        return CursorWrapper(cursor)

    def init_connection_state(self):
        pass

    def schema_editor(self, *args, **kwargs):
        "Returns a new instance of this backend's SchemaEditor"
        return DatabaseSchemaEditor(self, *args, **kwargs)

    def is_usable(self):
        try:
            self.connection.cursor().execute("SELECT 1")
        except DatabaseError:
            return False
        else:
            return True
