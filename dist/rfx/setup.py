from setuptools import setup
setup(
  name = 'rfx',
  packages = ['rfx'],
  version = "1611.0000",
  description = 'Container Config and Secret Management - core lib',
  author = 'Brandon Gillespie',
  author_email = 'bjg-pypi@cold.org',
  url = 'https://github.com/reflexsc/reflex', 
  keywords = ['docker','config','secrets'],
  install_requires = [
    'urllib3',
    'ujson',
    'pynacl',
    'requests',
    'setproctitle',
    'dictlib',
    'onetimejwt',
    'timeinterval'
  ],
  classifiers = [],
)

