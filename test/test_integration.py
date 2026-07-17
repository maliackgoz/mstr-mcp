import os
import sys
import unittest
import logging
from dotenv import load_dotenv

# Load environment variables from local .env
load_dotenv()

# Ensure src/ is in the Python path for import compatibility
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from mstrio.connection import Connection
from mstrio.server.project import list_projects
from mstrio.project_objects.report import list_reports, Report
from mstrio.project_objects import OlapCube, SuperCube

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mstr_integration_test")

class TestMstrAPIIntegration(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        # Read configuration from environment variables
        cls.base_url = os.getenv("MSTR_BASE_URL")
        cls.username = os.getenv("MSTR_USERNAME")
        cls.password = os.getenv("MSTR_PASSWORD")
        cls.project_id = os.getenv("MSTR_PROJECT_ID")
        cls.ssl_verify = os.getenv("MSTR_SSL_VERIFY", "True").lower() in ("true", "1", "yes")
        
        # Verify mandatory parameters are present
        if not cls.base_url or not cls.username or not cls.password:
            raise unittest.SkipTest(
                "Skipping integration tests: Missing mandatory env variables "
                "(MSTR_BASE_URL, MSTR_USERNAME, MSTR_PASSWORD)."
            )
            
        logger.info(f"Using MicroStrategy Library URL: {cls.base_url}")
        masked_user = cls.username[:3] + "***" if len(cls.username) > 3 else "***"
        logger.info(f"Authenticating as user: {masked_user}")
        
    def test_01_connection_and_auth(self):
        """Test establishing connection and LDAP authentication (loginMode: 16)"""
        try:
            conn = Connection(
                base_url=self.base_url,
                username=self.username,
                password=self.password,
                login_mode=16,  # LDAP
                ssl_verify=self.ssl_verify
            )
            self.assertTrue(conn.is_alive(), "MicroStrategy connection is not active.")
            logger.info("Connection & LDAP Authentication successful.")
            conn.close()
        except Exception as e:
            self.fail(f"Failed to connect/authenticate: {e}")
            
    def test_02_list_projects(self):
        """Test listing available projects"""
        try:
            conn = Connection(
                base_url=self.base_url,
                username=self.username,
                password=self.password,
                login_mode=16,
                ssl_verify=self.ssl_verify
            )
            projects = list_projects(connection=conn)
            logger.info(f"Found {len(projects)} projects.")
            for p in projects:
                logger.info(f" - Project Name: {p.name} | ID: {p.id}")
            self.assertIsNotNone(projects, "Failed to retrieve projects list.")
            conn.close()
        except Exception as e:
            self.fail(f"Failed to list projects: {e}")

    def test_03_list_reports(self):
        """Test listing reports and cubes inside the project"""
        if not self.project_id:
            self.skipTest("MSTR_PROJECT_ID not set. Skipping report listing test.")
            
        try:
            conn = Connection(
                base_url=self.base_url,
                username=self.username,
                password=self.password,
                project_id=self.project_id,
                login_mode=16,
                ssl_verify=self.ssl_verify
            )
            reports = list_reports(connection=conn)
            logger.info(f"Found {len(reports)} reports in project {self.project_id}.")
            for r in reports[:10]: # Print first 10 reports
                logger.info(f" - Report Name: {r.name} | ID: {r.id}")
                
            self.assertIsNotNone(reports)
            conn.close()
        except Exception as e:
            self.fail(f"Failed to list reports in project: {e}")

if __name__ == "__main__":
    unittest.main()
