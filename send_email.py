"""send_email.py

Single-file utility to send email via the OWA JSON endpoints used by the
browser. Reads `COOKIE_STRING` and optional headers from `.env`.

Usage (after filling .env and installing requirements):
  source .venv/bin/activate
  python send_email.py

If `CREATE_ACTION_ID` and `UPDATE_ACTION_ID` are present in the
environment, the script will perform a CreateItem (save draft) then
UpdateItem (send) flow using those action IDs and canary values. If
those are not present, the script attempts a single-step CreateItem
with MessageDisposition=SendAndSaveCopy.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv


def session_from_cookie_string(cookie_string: str, domain: str = "magnolia.amazon.com") -> requests.Session:
    """Create a requests.Session from a browser cookie string.

    The cookie string can be raw name=value pairs separated by semicolons.
    If the string is wrapped in quotes in the .env, they are stripped.
    """
    if not cookie_string:
        raise ValueError("cookie_string is empty")
    cookie_string = cookie_string.strip()
    # strip surrounding single/double quotes if present
    if (cookie_string.startswith("'") and cookie_string.endswith("'")) or (
        cookie_string.startswith('"') and cookie_string.endswith('"')
    ):
        cookie_string = cookie_string[1:-1]

    sess = requests.Session()
    for part in cookie_string.split(";"):
        if "=" in part:
            name, val = part.strip().split("=", 1)
            sess.cookies.set(name, val, domain=domain)
    return sess


def send_owa_action(
    session: requests.Session,
    action: str,
    action_id: str,
    payload: Dict,
    client_request_id: Optional[str] = None,
    action_name: Optional[str] = None,
    owa_canary: Optional[str] = None,
    origin: str = "https://magnolia.amazon.com",
    timeout: int = 30,
) -> Dict:
    """Send a generic OWA action (CreateItem / UpdateItem).

    Returns a structured dict: {status_code, data, headers} where data is
    parsed JSON when possible or raw text otherwise.
    """
    url = f"https://magnolia.amazon.com/owa/service.svc?action={action}&ID={action_id}&AC=1"

    headers = {
        "Accept": "*/*",
        "Content-Type": "application/json; charset=UTF-8",
        "Origin": origin,
        "X-Requested-With": "XMLHttpRequest",
    }
    # The OWA backend expects an `Action` header with the action name
    # (e.g. CreateItem / UpdateItem). Without it the server returns 500.
    headers["Action"] = action
    if client_request_id:
        headers["client-request-id"] = client_request_id
        headers["x-owa-correlationid"] = client_request_id
    if action_name:
        headers["x-owa-actionname"] = action_name
    if action_id:
        headers["x-owa-actionid"] = str(action_id)
    if owa_canary:
        headers["x-owa-canary"] = owa_canary

    resp = session.post(url, json=payload, headers=headers, timeout=timeout)
    result = {"status_code": resp.status_code}
    try:
        result["data"] = resp.json()
    except ValueError:
        result["data"] = resp.text
    result["headers"] = dict(resp.headers)
    return result


def build_create_payload(to_addrs: List[str], subject: str, body_html: str, send_direct: bool = False) -> Dict:
    """Construct a CreateItem JSON payload. If send_direct=True sets
    MessageDisposition to SendAndSaveCopy (attempt single-step send).
    """
    to_recipients = [
        {"Name": addr, "EmailAddress": addr, "RoutingType": "SMTP", "MailboxType": "Mailbox", "RelevanceScore": 2147483646}
        for addr in to_addrs
    ]

    body = {
        "__type": "CreateItemJsonRequest:#Exchange",
        "Header": {
            "__type": "JsonRequestHeaders:#Exchange",
            "RequestServerVersion": "V2015_10_15",
            "TimeZoneContext": {"__type": "TimeZoneContext:#Exchange", "TimeZoneDefinition": {"__type": "TimeZoneDefinitionType:#Exchange", "Id": "Pacific Standard Time"}},
        },
        "Body": {
            "__type": "CreateItemRequest:#Exchange",
            "Items": [
                {
                    "__type": "Message:#Exchange",
                    "Subject": subject,
                    "Body": {"__type": "BodyContentType:#Exchange", "BodyType": "HTML", "Value": body_html},
                    "Importance": "Normal",
                    "From": None,
                    "ToRecipients": to_recipients,
                    "CcRecipients": [],
                    "BccRecipients": [],
                    "Sensitivity": "Normal",
                    "IsDeliveryReceiptRequested": False,
                    "IsReadReceiptRequested": False,
                    "PendingSocialActivityTagIds": [],
                }
            ],
            "ClientSupportsIrm": True,
            "OutboundCharset": "AutoDetect",
            "PromoteEmojiContentToInlineAttachmentsCount": 0,
            "UnpromotedInlineImageCount": 0,
            # if send_direct -> attempt to send in one step
            "MessageDisposition": "SendAndSaveCopy" if send_direct else "SaveOnly",
            "ComposeOperation": "newMail",
        },
    }
    return body


def find_itemid(obj) -> Optional[Tuple[str, Optional[str]]]:
    """Search response object for an ItemId dict and return (Id, ChangeKey)."""
    if isinstance(obj, dict):
        if "ItemId" in obj and isinstance(obj["ItemId"], dict):
            iid = obj["ItemId"]
            if "Id" in iid:
                return iid.get("Id"), iid.get("ChangeKey")
        for v in obj.values():
            res = find_itemid(v)
            if res:
                return res
    elif isinstance(obj, list):
        for item in obj:
            res = find_itemid(item)
            if res:
                return res
    return None


def main() -> int:
    load_dotenv()
    cookie_string = os.getenv("COOKIE_STRING")
    if not cookie_string:
        print("COOKIE_STRING not set in .env. Exiting.")
        return 1

    to_addrs_raw = os.getenv("TO_ADDRS")
    if not to_addrs_raw:
        print("TO_ADDRS not set in .env. Exiting.")
        return 1
    to_addrs = [s.strip() for s in to_addrs_raw.split(",") if s.strip()]

    subject = os.getenv("SUBJECT", "Test email from script")
    body = os.getenv("BODY", "<p>Test</p><p>Thanks,</p><p>Your script</p>")

    # optional headers used by the browser flow
    create_action_id = os.getenv("CREATE_ACTION_ID")
    create_action_name = os.getenv("CREATE_ACTION_NAME")
    create_client_request_id = os.getenv("CREATE_CLIENT_REQUEST_ID")
    create_owa_canary = os.getenv("CREATE_OWA_CANARY")

    update_action_id = os.getenv("UPDATE_ACTION_ID")
    update_action_name = os.getenv("UPDATE_ACTION_NAME")
    update_client_request_id = os.getenv("UPDATE_CLIENT_REQUEST_ID")
    update_owa_canary = os.getenv("UPDATE_OWA_CANARY")

    session = session_from_cookie_string(cookie_string)

    # If both create and update action info present, use two-step flow
    if create_action_id and update_action_id:
        print("Creating draft (CreateItem) for:", to_addrs)
        create_payload = build_create_payload(to_addrs, subject, body, send_direct=False)
        create_resp = send_owa_action(
            session=session,
            action="CreateItem",
            action_id=create_action_id,
            payload=create_payload,
            client_request_id=create_client_request_id,
            action_name=create_action_name,
            owa_canary=create_owa_canary,
        )

        if create_resp.get("status_code", 0) >= 400:
            print("CreateItem error status:", create_resp.get("status_code"))
            print(json.dumps(create_resp, indent=2))
            return 2

        itemid = find_itemid(create_resp.get("data", {}))
        if not itemid:
            print("Could not find ItemId in CreateItem response. Response:")
            print(json.dumps(create_resp, indent=2))
            return 3
        item_id_val, change_key = itemid
        print("Created item id:", item_id_val)

        # Build UpdateItem payload to set To/Subject/Body and send
        update_payload = {
            "__type": "UpdateItemJsonRequest:#Exchange",
            "Header": {
                "__type": "JsonRequestHeaders:#Exchange",
                "RequestServerVersion": "Exchange2015",
                "TimeZoneContext": {"__type": "TimeZoneContext:#Exchange", "TimeZoneDefinition": {"__type": "TimeZoneDefinitionType:#Exchange", "Id": "Pacific Standard Time"}},
            },
            "Body": {
                "__type": "UpdateItemRequest:#Exchange",
                "ItemChanges": [
                    {
                        "__type": "ItemChange:#Exchange",
                        "Updates": [
                            {"__type": "SetItemField:#Exchange", "Path": {"__type": "PropertyUri:#Exchange", "FieldURI": "ToRecipients"}, "Item": {"__type": "Message:#Exchange", "ToRecipients": [{"Name": addr, "EmailAddress": addr, "RoutingType": "SMTP", "MailboxType": "Mailbox", "RelevanceScore": 2147483646} for addr in to_addrs]}},
                            {"__type": "SetItemField:#Exchange", "Path": {"__type": "PropertyUri:#Exchange", "FieldURI": "Subject"}, "Item": {"__type": "Message:#Exchange", "Subject": subject}},
                            {"__type": "SetItemField:#Exchange", "Path": {"__type": "PropertyUri:#Exchange", "FieldURI": "Body"}, "Item": {"__type": "Message:#Exchange", "Body": {"__type": "BodyContentType:#Exchange", "BodyType": "HTML", "Value": body}}},
                        ],
                        "ItemId": {"__type": "ItemId:#Exchange", "Id": item_id_val, "ChangeKey": change_key},
                    }
                ],
                "ConflictResolution": "AlwaysOverwrite",
                "ClientSupportsIrm": True,
                "SendCalendarInvitationsOrCancellations": "SendToNone",
                "MessageDisposition": "SendAndSaveCopy",
                "SuppressReadReceipts": False,
            },
        }

        print("Sending (UpdateItem) item id:", item_id_val)
        update_resp = send_owa_action(
            session=session,
            action="UpdateItem",
            action_id=update_action_id,
            payload=update_payload,
            client_request_id=update_client_request_id,
            action_name=update_action_name,
            owa_canary=update_owa_canary,
        )

        print(json.dumps(update_resp, indent=2))
        return 0 if update_resp.get("status_code", 0) < 400 else 4

    # Fallback: single-step CreateItem with MessageDisposition SendAndSaveCopy
    print("Attempting single-step CreateItem (send) for:", to_addrs)
    send_payload = build_create_payload(to_addrs, subject, body, send_direct=True)
    send_resp = send_owa_action(session=session, action="CreateItem", action_id="-1", payload=send_payload)
    print(json.dumps(send_resp, indent=2))
    return 0 if send_resp.get("status_code", 0) < 400 else 5


if __name__ == "__main__":
    sys.exit(main())
