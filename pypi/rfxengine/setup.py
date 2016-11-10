from setuptools import setup
setup(
  name = 'rfxengine',
  package_dir = {
    'rfxengine.db': 'rfxengine/db',
    'rfxengine.server': 'rfxengine/server',
  },
  packages = ['rfxengine', 'rfxengine.db', 'rfxengine.server'],
  version = "1611.0006",
  description = 'Container Config and Secret Management - engine',
  author = 'Brandon Gillespie',
  author_email = 'bjg-pypi@cold.org',
  url = 'https://reflex.cold.org/',
  keywords = ['docker','config','secrets'],
  install_requires = [
    'rfx',
    'cherrypy'
  ],
  entry_points = {
    'console_scripts': [
      'reflex-engine=rfxengine.server.cherry:main',
    ]
  },
  classifiers = [],
)
