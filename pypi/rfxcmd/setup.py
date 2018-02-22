from setuptools import setup
setup(
  name = 'rfxcmd',
  packages = ['rfxcmd'],
  version = "1802.0003",
  description = 'Reflex - Container Config and Secret Management - commands',
  author = 'Brandon Gillespie',
  author_email = 'bjg-pypi@cold.org',
  url = 'https://reflex.cold.org/', 
  keywords = ['docker','config','secrets'],
  install_requires = [
    'rfx',
  ],
  entry_points = {
    'console_scripts': [
      'reflex=rfxcmd:main',
      'act=rfxcmd:main',
      'action=rfxcmd:main',
      'apikey=rfxcmd:main',
      'app=rfxcmd:main',
      'engine=rfxcmd:main',
      'launch=rfxcmd:main',
      'rxe=rfxcmd:main'
    ]
  },
  classifiers = [],
)

