from setuptools import setup
setup(
  name = 'rfxengine',
  packages = ['rfxengine'],
  version = "1611.0003",
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

