"""send_email.py

Utility to send email via the OWA JSON endpoints used by the browser.
Supports To, CC, and BCC recipients.

CLI Usage (reads config from .env):
  source .venv/bin/activate
  python send_email.py

Programmatic Usage:
  from send_email import send_email
  
  result = send_email(
      to_addrs=["user@amazon.com"],
      cc_addrs=["cc@amazon.com"],
      bcc_addrs=["bcc@amazon.com"],
      subject="Test",
      body_html="<p>Hello</p>"
  )
  
  if result["success"]:
      print("Sent!")

The script can use either a two-step flow (CreateItem → UpdateItem) if
CREATE_ACTION_ID and UPDATE_ACTION_ID are set, or a single-step
CreateItem with MessageDisposition=SendAndSaveCopy.
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
    action_name: Optional[str] = None,
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
    if action_name:
        headers["x-owa-actionname"] = action_name
    if action_id:
        headers["x-owa-actionid"] = str(action_id)
    # If the OWA canary is present as a cookie (browser includes it in -b),
    # include it as a header as the service expects it there as well.
    try:
        # session.cookies is a RequestsCookieJar; iterate to find case-insensitive match
        for c in session.cookies:
            if c.name.lower() == "x-owa-canary":
                headers["x-owa-canary"] = c.value
                break
    except Exception:
        # don't fail if cookies can't be iterated for some reason
        pass
    resp = session.post(url, json=payload, headers=headers, timeout=timeout)
    result = {"status_code": resp.status_code}
    try:
        result["data"] = resp.json()
    except ValueError:
        result["data"] = resp.text
    result["headers"] = dict(resp.headers)
    return result


def build_payload(
    action_type: str,
    to_addrs: List[str],
    subject: str,
    body_html: str,
    cc_addrs: Optional[List[str]] = None,
    bcc_addrs: Optional[List[str]] = None,
    send_direct: bool = False,
    item_id: Optional[str] = None,
    change_key: Optional[str] = None,
) -> Dict:
    """Build either a CreateItem or UpdateItem JSON payload.

    - action_type: "CreateItem" or "UpdateItem"
    - to_addrs: List of To recipient email addresses
    - cc_addrs: Optional list of CC recipient email addresses
    - bcc_addrs: Optional list of BCC recipient email addresses
    - For CreateItem, set `send_direct=True` to use MessageDisposition=SendAndSaveCopy.
    - For UpdateItem, supply `item_id` (and optionally `change_key`).
    """
    to_recipients = [
        {"Name": addr, "EmailAddress": addr, "RoutingType": "SMTP", "MailboxType": "Mailbox", "RelevanceScore": 2147483646}
        for addr in to_addrs
    ]
    cc_recipients = [
        {"Name": addr, "EmailAddress": addr, "RoutingType": "SMTP", "MailboxType": "Mailbox", "RelevanceScore": 2147483646}
        for addr in (cc_addrs or [])
    ]
    bcc_recipients = [
        {"Name": addr, "EmailAddress": addr, "RoutingType": "SMTP", "MailboxType": "Mailbox", "RelevanceScore": 2147483646}
        for addr in (bcc_addrs or [])
    ]

    if action_type == "CreateItem":
        return {
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
                        "CcRecipients": cc_recipients,
                        "BccRecipients": bcc_recipients,
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
                "MessageDisposition": "SendAndSaveCopy" if send_direct else "SaveOnly",
                "ComposeOperation": "newMail",
            },
        }

    if action_type == "UpdateItem":
        if not item_id:
            raise ValueError("item_id is required for UpdateItem payload")
        return {
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
                            {
                                "__type": "SetItemField:#Exchange",
                                "Path": {"__type": "PropertyUri:#Exchange", "FieldURI": "ToRecipients"},
                                "Item": {"__type": "Message:#Exchange", "ToRecipients": to_recipients},
                            },
                            {
                                "__type": "SetItemField:#Exchange",
                                "Path": {"__type": "PropertyUri:#Exchange", "FieldURI": "CcRecipients"},
                                "Item": {"__type": "Message:#Exchange", "CcRecipients": cc_recipients},
                            },
                            {
                                "__type": "SetItemField:#Exchange",
                                "Path": {"__type": "PropertyUri:#Exchange", "FieldURI": "BccRecipients"},
                                "Item": {"__type": "Message:#Exchange", "BccRecipients": bcc_recipients},
                            },
                            {"__type": "SetItemField:#Exchange", "Path": {"__type": "PropertyUri:#Exchange", "FieldURI": "Subject"}, "Item": {"__type": "Message:#Exchange", "Subject": subject}},
                            {"__type": "SetItemField:#Exchange", "Path": {"__type": "PropertyUri:#Exchange", "FieldURI": "Body"}, "Item": {"__type": "Message:#Exchange", "Body": {"__type": "BodyContentType:#Exchange", "BodyType": "HTML", "Value": body_html}}},
                        ],
                        "ItemId": {"__type": "ItemId:#Exchange", "Id": item_id, "ChangeKey": change_key},
                    }
                ],
                "ConflictResolution": "AlwaysOverwrite",
                "ClientSupportsIrm": True,
                "SendCalendarInvitationsOrCancellations": "SendToNone",
                "MessageDisposition": "SendAndSaveCopy",
                "SuppressReadReceipts": False,
            },
        }

    raise ValueError(f"Unsupported action_type: {action_type}")


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


def send_email(
    to_addrs: List[str],
    subject: str,
    body_html: str,
    cc_addrs: Optional[List[str]] = None,
    bcc_addrs: Optional[List[str]] = None,
    cookie_string: Optional[str] = None,
    create_action_id: Optional[str] = None,
    create_action_name: Optional[str] = None,
    update_action_id: Optional[str] = None,
    update_action_name: Optional[str] = None,
) -> Dict:
    """Send an email via OWA.

    Args:
        to_addrs: List of To recipient email addresses (required)
        subject: Email subject
        body_html: Email body in HTML format
        cc_addrs: Optional list of CC recipient email addresses
        bcc_addrs: Optional list of BCC recipient email addresses
        cookie_string: Browser cookie string. If None, reads from COOKIE_STRING env var
        create_action_id: Optional CreateItem action ID. If None, reads from env
        create_action_name: Optional CreateItem action name. If None, reads from env
        update_action_id: Optional UpdateItem action ID. If None, reads from env
        update_action_name: Optional UpdateItem action name. If None, reads from env

    Returns:
        Dict with keys: status_code, data, headers, success (bool)

    Example:
        result = send_email(
            to_addrs=["user@example.com"],
            subject="Test",
            body_html="<p>Hello</p>",
            cc_addrs=["cc@example.com"],
            bcc_addrs=["bcc@example.com"]
        )
        if result["success"]:
            print("Email sent successfully!")
    """
    # Load env if cookie_string not provided
    if cookie_string is None:
        load_dotenv()
        cookie_string = os.getenv("COOKIE_STRING")
    
    if not cookie_string:
        return {"success": False, "error": "COOKIE_STRING not provided or not set in .env", "status_code": 0}

    if not to_addrs:
        return {"success": False, "error": "to_addrs cannot be empty", "status_code": 0}

    # Get action IDs from env if not provided
    if create_action_id is None:
        create_action_id = os.getenv("CREATE_ACTION_ID")
    if create_action_name is None:
        create_action_name = os.getenv("CREATE_ACTION_NAME")
    if update_action_id is None:
        update_action_id = os.getenv("UPDATE_ACTION_ID")
    if update_action_name is None:
        update_action_name = os.getenv("UPDATE_ACTION_NAME")

    session = session_from_cookie_string(cookie_string)

    # If both create and update action info present, use two-step flow
    if create_action_id and update_action_id:
        # Step 1: CreateItem (save draft)
        create_payload = build_payload(
            "CreateItem", to_addrs, subject, body_html, cc_addrs=cc_addrs, bcc_addrs=bcc_addrs, send_direct=False
        )
        create_resp = send_owa_action(
            session=session,
            action="CreateItem",
            action_id=create_action_id,
            payload=create_payload,
            action_name=create_action_name,
        )

        if create_resp.get("status_code", 0) >= 400:
            create_resp["success"] = False
            create_resp["error"] = "CreateItem failed"
            return create_resp

        itemid = find_itemid(create_resp.get("data", {}))
        if not itemid:
            return {
                "success": False,
                "error": "Could not find ItemId in CreateItem response",
                "status_code": create_resp.get("status_code"),
                "data": create_resp.get("data"),
            }
        item_id_val, change_key = itemid

        # Step 2: UpdateItem (send)
        update_payload = build_payload(
            "UpdateItem", to_addrs, subject, body_html, cc_addrs=cc_addrs, bcc_addrs=bcc_addrs, 
            item_id=item_id_val, change_key=change_key
        )
        update_resp = send_owa_action(
            session=session,
            action="UpdateItem",
            action_id=update_action_id,
            payload=update_payload,
            action_name=update_action_name,
        )
        update_resp["success"] = update_resp.get("status_code", 0) < 400
        if not update_resp["success"]:
            update_resp["error"] = "UpdateItem (send) failed"
        return update_resp

    # Fallback: single-step CreateItem with MessageDisposition SendAndSaveCopy
    send_payload = build_payload(
        "CreateItem", to_addrs, subject, body_html, cc_addrs=cc_addrs, bcc_addrs=bcc_addrs, send_direct=True
    )
    send_resp = send_owa_action(
        session=session,
        action="CreateItem",
        action_id="-1",
        payload=send_payload,
        action_name=create_action_name,
    )
    send_resp["success"] = send_resp.get("status_code", 0) < 400
    if not send_resp["success"]:
        send_resp["error"] = "Single-step CreateItem (send) failed"
    return send_resp


def usage_from_env() -> int:
    """CLI entry point - reads config from .env and sends email."""
    load_dotenv()
    
    to_addrs_raw = os.getenv("TO_ADDRS")
    if not to_addrs_raw:
        print("TO_ADDRS not set in .env. Exiting.")
        return 1
    to_addrs = [s.strip() for s in to_addrs_raw.split(",") if s.strip()]

    # Optional CC and BCC from env
    cc_addrs = None
    cc_addrs_raw = os.getenv("CC_ADDRS")
    if cc_addrs_raw:
        cc_addrs = [s.strip() for s in cc_addrs_raw.split(",") if s.strip()]
    
    bcc_addrs = None
    bcc_addrs_raw = os.getenv("BCC_ADDRS")
    if bcc_addrs_raw:
        bcc_addrs = [s.strip() for s in bcc_addrs_raw.split(",") if s.strip()]

    subject = os.getenv("SUBJECT")
    body = os.getenv("BODY")

    if not subject or not body:
        print("SUBJECT and BODY must be set in .env. Exiting.")
        return 1

    print(f"Sending email to: {to_addrs}")
    if cc_addrs:
        print(f"CC: {cc_addrs}")
    if bcc_addrs:
        print(f"BCC: {bcc_addrs}")
    
    result = send_email(
        to_addrs=to_addrs,
        subject=subject,
        body_html=body,
        cc_addrs=cc_addrs,
        bcc_addrs=bcc_addrs,
    )
    
    print(json.dumps(result, indent=2))
    return 0 if result.get("success") else 1


def main():
    pass


if __name__ == "__main__":
    sys.exit(usage_from_env())
