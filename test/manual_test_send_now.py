#!/usr/bin/env python3
"""
MicroStrategy (MSTR) REST API Manual Test Script - Send Prompted Document Now

This script performs the following steps:
1. Authenticates using LDAP credentials (loginMode: 16) and gets a session token.
2. Retrieves the current user's profile ID.
3. Checks if the user's profile already has the specified recipient email address.
   If not, it lists the email devices, finds 'Generic email' or equivalent,
   and registers the email address under the user's profile.
4. Executes the prompted document to create a document instance.
5. Programmatically answers prompts on the document instance (if configured).
6. Creates a subscription with `sendNow: True` to trigger immediate email delivery.
7. Logs out and cleans up.

Configuration is loaded from environment variables (e.g., from a local .env file).
"""

import os
import sys
import logging
import requests
import urllib3
from dotenv import load_dotenv

# Configure clean console logging for the test run
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("mstr_manual_test")

# Load environment variables
load_dotenv()

# =====================================================================
# Configuration & Sample Prompt Answers
# =====================================================================
MSTR_BASE_URL = os.getenv("MSTR_BASE_URL", "https://localhost/MicroStrategyLibrary/api")
MSTR_USERNAME = os.getenv("MSTR_USERNAME")
MSTR_PASSWORD = os.getenv("MSTR_PASSWORD")
MSTR_PROJECT_ID = os.getenv("MSTR_PROJECT_ID")
MSTR_SSL_VERIFY = os.getenv("MSTR_SSL_VERIFY", "True").lower() in ("true", "1", "yes")

# Target settings for document delivery
DOCUMENT_ID = os.getenv("MSTR_TEST_DOCUMENT_ID") or os.getenv("DOCUMENT_ID")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")
CONTENT_TYPE = os.getenv("CONTENT_TYPE", "document")  # e.g., 'document' or 'dossier' or 'report'

# Example of custom prompt answers structure. Customize this block to fit your specific prompts.
# Refer to PromptAnswering schema for VALUE, ELEMENTS, and OBJECTS types.
PROMPT_ANSWERS = [
    # Example 1: Value Prompt (Text, Date, or Numeric)
    # {
    #     "key": "A1B2C3D4E5F6G7H8I9J0",  # Unique key of the prompt (often same as prompt object ID or index key)
    #     "id": "A1B2C3D4E5F6G7H8I9J0",   # Object ID of the prompt
    #     "name": "Date Select Prompt",
    #     "type": "VALUE",
    #     "useDefault": False,
    #     "answers": {
    #         "value": "2026-07-17"
    #     }
    # },
    # Example 2: Attribute Elements Prompt
    # {
    #     "key": "E1F2G3H4I5J6K7L8M9N0",
    #     "id": "E1F2G3H4I5J6K7L8M9N0",
    #     "name": "Region Elements Prompt",
    #     "type": "ELEMENTS",
    #     "useDefault": False,
    #     "answers": [
    #         {
    #             "id": "8D679D3C11D3E4981000E787EC6DE8A4:7796", # Format: <attribute ID>:<element ID>
    #             "name": "Northeast"
    #         }
    #     ]
    # }
]

def main():
    # Validation
    missing = []
    if not MSTR_USERNAME: missing.append("MSTR_USERNAME")
    if not MSTR_PASSWORD: missing.append("MSTR_PASSWORD")
    if not DOCUMENT_ID: missing.append("DOCUMENT_ID or MSTR_TEST_DOCUMENT_ID")
    if not RECIPIENT_EMAIL: missing.append("RECIPIENT_EMAIL")
    if not MSTR_PROJECT_ID: missing.append("MSTR_PROJECT_ID")

    if missing:
        logger.error(f"Missing mandatory environment variables for test execution: {', '.join(missing)}")
        logger.error("Please add them to your environment or a local .env file.")
        sys.exit(1)

    if not MSTR_SSL_VERIFY:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        logger.warning("SSL verification is disabled (MSTR_SSL_VERIFY=False).")

    # Set up session
    session = requests.Session()
    session.headers.update({
        "Content-Type": "application/json",
        "Accept": "application/json"
    })

    # Mask credentials for printing
    masked_user = MSTR_USERNAME[:3] + "***" if len(MSTR_USERNAME) > 3 else "***"
    logger.info(f"Using Library API base URL: {MSTR_BASE_URL}")
    logger.info(f"Targeting Project ID: {MSTR_PROJECT_ID}")
    logger.info(f"Targeting Document ID: {DOCUMENT_ID}")
    logger.info(f"Targeting Recipient Email: {RECIPIENT_EMAIL}")

    # =====================================================================
    # Step 1: Login
    # =====================================================================
    logger.info(f"Step 1: Logging in as user '{masked_user}' using LDAP Auth...")
    login_url = f"{MSTR_BASE_URL}/auth/login"
    login_payload = {
        "username": MSTR_USERNAME,
        "password": MSTR_PASSWORD,
        "loginMode": 16  # LDAP
    }

    try:
        res = session.post(login_url, json=login_payload, verify=MSTR_SSL_VERIFY, timeout=30)
        res.raise_for_status()
    except Exception as e:
        logger.error(f"Authentication POST failed: {e}")
        if 'res' in locals() and res.text:
            logger.error(f"Server Response: {res.text}")
        sys.exit(1)

    auth_token = res.headers.get("X-MSTR-AuthToken")
    if not auth_token:
        logger.error("Login succeeded, but X-MSTR-AuthToken header was missing from the response.")
        sys.exit(1)

    logger.info("Successfully authenticated. X-MSTR-AuthToken obtained.")
    session.headers.update({"X-MSTR-AuthToken": auth_token})

    try:
        # =====================================================================
        # Step 2: Get Current User Information
        # =====================================================================
        logger.info("Step 2: Retrieving current authenticated user details...")
        user_info_url = f"{MSTR_BASE_URL}/sessions/userInfo"
        res = session.get(user_info_url, verify=MSTR_SSL_VERIFY, timeout=15)
        res.raise_for_status()
        user_info = res.json()
        user_id = user_info.get("id")
        user_fullname = user_info.get("fullName", "N/A")
        logger.info(f"User identity resolved: ID={user_id} | Name={user_fullname}")

        # =====================================================================
        # Step 3: Resolve / Register Recipient Address
        # =====================================================================
        logger.info(f"Step 3: Checking if recipient email '{RECIPIENT_EMAIL}' is registered under user profile...")
        addresses_url = f"{MSTR_BASE_URL}/users/{user_id}/addresses"
        res = session.get(addresses_url, verify=MSTR_SSL_VERIFY, timeout=15)
        res.raise_for_status()
        
        # MicroStrategy can return either a list directly or a wrapped dictionary depending on version
        address_list = res.json()
        if isinstance(address_list, dict):
            address_list = address_list.get("addresses", [])

        address_id = None
        for addr in address_list:
            if addr.get("deliveryMode") == "EMAIL" and addr.get("value", "").lower() == RECIPIENT_EMAIL.lower():
                address_id = addr.get("id")
                logger.info(f"Found existing registered address. Address ID: {address_id}")
                break

        if not address_id:
            logger.info("Email address not found in profile. Searching for an Email Device to register a new address...")
            # Query email devices
            devices_url = f"{MSTR_BASE_URL}/v2/devices"
            res = session.get(devices_url, params={"deviceType": "email"}, verify=MSTR_SSL_VERIFY, timeout=15)
            res.raise_for_status()
            
            device_data = res.json()
            devices = device_data.get("devices", []) if isinstance(device_data, dict) else device_data
            
            if not devices:
                logger.error("No email devices found on the MicroStrategy server. Unable to register new email address.")
                sys.exit(1)

            # Match "Generic email" or take first email device
            device_id = None
            for dev in devices:
                if dev.get("name", "").lower() == "generic email":
                    device_id = dev.get("id")
                    logger.info(f"Using 'Generic email' device (ID: {device_id})")
                    break
            
            if not device_id:
                device_id = devices[0].get("id")
                device_name = devices[0].get("name", "Unknown")
                logger.info(f"Using default email device '{device_name}' (ID: {device_id})")

            logger.info(f"Registering address '{RECIPIENT_EMAIL}' under user profile...")
            address_payload = {
                "name": f"Manual Test - {RECIPIENT_EMAIL}",
                "deliveryMode": "EMAIL",
                "deviceId": device_id,
                "value": RECIPIENT_EMAIL,
                "isDefault": False
            }
            res = session.post(addresses_url, json=address_payload, verify=MSTR_SSL_VERIFY, timeout=15)
            res.raise_for_status()
            created_address = res.json()
            address_id = created_address.get("id")
            logger.info(f"Address registered successfully. Address ID: {address_id}")

        # Add project context header for project-specific operations
        session.headers.update({"X-MSTR-ProjectID": MSTR_PROJECT_ID})

        # =====================================================================
        # Step 4: Create Document Instance
        # =====================================================================
        logger.info(f"Step 4: Executing document '{DOCUMENT_ID}' to create an instance...")
        instance_url = f"{MSTR_BASE_URL}/documents/{DOCUMENT_ID}/instances"
        res = session.post(instance_url, json={}, verify=MSTR_SSL_VERIFY, timeout=30)
        res.raise_for_status()
        
        instance_details = res.json()
        instance_id = instance_details.get("mid")
        is_prompted = instance_details.get("prompted", False)
        logger.info(f"Document instance created successfully. Instance ID: {instance_id}")
        logger.info(f"Is instance prompted: {is_prompted}")

        # =====================================================================
        # Step 5: Answer Prompts
        # =====================================================================
        if is_prompted:
            logger.info("Step 5: Document is prompted. Preparing to apply prompt answers...")
            if not PROMPT_ANSWERS:
                logger.warning("Document is prompted but no prompt answers were configured in PROMPT_ANSWERS.")
                logger.warning("Will attempt to proceed with default answers or trigger failure if required prompts are unanswered.")
            else:
                prompt_answers_url = f"{MSTR_BASE_URL}/documents/{DOCUMENT_ID}/instances/{instance_id}/prompts/answers"
                prompt_payload = {
                    "prompts": PROMPT_ANSWERS
                }
                res = session.put(prompt_answers_url, json=prompt_payload, verify=MSTR_SSL_VERIFY, timeout=20)
                if res.status_code == 204:
                    logger.info("Prompt answers applied successfully (HTTP 204).")
                else:
                    res.raise_for_status()
                    logger.info(f"Prompt answers applied. Status Code: {res.status_code}")
        else:
            logger.info("Step 5: Skipping prompt answering (document is not prompted).")

        # =====================================================================
        # Step 6: Create Subscription & Send Now
        # =====================================================================
        logger.info("Step 6: Triggering immediate email subscription ('Send Now')...")
        subscriptions_url = f"{MSTR_BASE_URL}/subscriptions"
        
        subscription_payload = {
            "name": f"Send Now Test - Document {DOCUMENT_ID}",
            "sendNow": True,
            "contents": [
                {
                    "id": DOCUMENT_ID,
                    "type": CONTENT_TYPE,
                    "personalization": {
                        "prompt": {
                            "enabled": True if is_prompted else False,
                            "instanceId": instance_id
                        }
                    }
                }
            ],
            "recipients": [
                {
                    "id": user_id,
                    "type": "user",
                    "addressId": address_id,
                    "includeType": "TO"
                }
            ],
            "delivery": {
                "mode": "EMAIL",
                "email": {
                    "subject": f"MSTR Send Now Manual Test: {DOCUMENT_ID}",
                    "message": "This is a prompted document delivered immediately via 'Send Now' REST API subscription call.",
                    "sendContentAs": "data"
                }
            }
        }

        res = session.post(subscriptions_url, json=subscription_payload, verify=MSTR_SSL_VERIFY, timeout=30)
        res.raise_for_status()
        
        subscription_res = res.json()
        subscription_id = subscription_res.get("id")
        logger.info("Immediate email delivery request accepted by MSTR server!")
        logger.info(f"Subscription ID created: {subscription_id}")
        logger.info("Manual test sequence successfully completed.")

    finally:
        # =====================================================================
        # Step 7: Logout
        # =====================================================================
        logger.info("Step 7: Logging out and closing session...")
        logout_url = f"{MSTR_BASE_URL}/auth/logout"
        try:
            res = session.post(logout_url, verify=MSTR_SSL_VERIFY, timeout=10)
            if res.status_code == 204:
                logger.info("Logged out successfully.")
            else:
                logger.info(f"Logout completed. Status Code: {res.status_code}")
        except Exception as logout_err:
            logger.warning(f"Error during logout: {logout_err}")

if __name__ == "__main__":
    main()
