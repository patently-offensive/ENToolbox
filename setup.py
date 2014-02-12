from setuptools import setup
readme = open('README.txt').read()
setup(name='ENToolbox',
      version='0.1',
      author='Scott Kammlade',
      author_email='noreply.garfield@gmail.com',
      license='MIT',
      description='A set of tools for interacting with Evernote API in a simplified manner',
      long_description=readme,
      py_modules=['ENToolbox'])