# coding: utf-8
from setuptools import setup

setup(
    name='django-vertica-backend',
    version='0.1.3',
    packages=['vertica'],
    url='https://github.com/tumb1er/django_vertica_backend',
    license='MIT',
    author='tumbler',
    author_email='zimbler@gmail.com',
    description='Vertica backend for Django',
    install_requires=[
        'vertica-python>=0.5',
        'psycopg2>=2.4.5'
    ]
)
