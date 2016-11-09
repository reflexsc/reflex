from setuptools import setup
setup(
  name = 'rfx',
  packages = ['rfx'],
  version = "1611.0000",
  description = 'Container Config and Secret Management - engine',
  author = 'Brandon Gillespie',
  author_email = 'bjg-pypi@cold.org',
  url = 'https://reflex.cold.org/', 
  keywords = ['docker','config','secrets'],
  install_requires = [
    'rfx',
  ],
  entry_points = {
    'console_scripts': [
      'reflex-engine=rfx.engine.cherry:main',
    ]
  },
  classifiers = [],
)

