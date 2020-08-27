import unittest
from click.testing import CliRunner
from flintdata.scripts.cli import *

class CliTestCase(unittest.TestCase):
	def test_cli_no_option(self):
		runner = CliRunner()
		result = runner.invoke(cli)
		self.assertEqual(result.exit_code,0)

	def test_cli_help(self):
		runner = CliRunner()
		result = runner.invoke(cli, ['--help'])
		self.assertEqual(result.exit_code,0)

	def test_cli_version(self):
		runner = CliRunner()
		result = runner.invoke(cli, ['--version'])
		self.assertEqual(result.output,'flintdata, version 1.0.0\n' )
		self.assertEqual(result.exit_code,0)

if __name__ == '__main__':
    	unittest.main()
