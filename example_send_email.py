#!/usr/bin/env python
"""Example usage of the send_email function.

This demonstrates how to use send_email.py as a library function
instead of just running it as a CLI script.
"""

from send_email import send_email

# Example 1: Simple email with To addresses only
def example_simple():
    result = send_email(
        to_addrs=["recipient@amazon.com"],
        subject="Simple Test Email",
        body_html="<p>Hello!</p><p>This is a test email.</p>",
    )
    
    if result["success"]:
        print("✓ Email sent successfully!")
    else:
        print(f"✗ Failed to send: {result.get('error')}")
    
    return result


# Example 2: Email with CC and BCC
def example_with_cc_bcc():
    result = send_email(
        to_addrs=["primary@amazon.com"],
        cc_addrs=["cc1@amazon.com", "cc2@amazon.com"],
        bcc_addrs=["bcc@amazon.com"],
        subject="Project Update with CC/BCC",
        body_html="""
        <h2>Project Status Update</h2>
        <p>Dear Team,</p>
        <p>This is an update on the current project status...</p>
        <ul>
            <li>Task 1: Completed</li>
            <li>Task 2: In Progress</li>
            <li>Task 3: Pending</li>
        </ul>
        <p>Best regards,<br>Your Team</p>
        """,
    )
    
    if result["success"]:
        print("✓ Email with CC/BCC sent successfully!")
    else:
        print(f"✗ Failed to send: {result.get('error')}")
    
    return result


# Example 3: Multiple To recipients
def example_multiple_recipients():
    result = send_email(
        to_addrs=[
            "user1@amazon.com",
            "user2@amazon.com",
            "user3@amazon.com"
        ],
        subject="Team Announcement",
        body_html="<p>Important team announcement...</p>",
    )
    
    if result["success"]:
        print("✓ Email sent to multiple recipients!")
    else:
        print(f"✗ Failed to send: {result.get('error')}")
    
    return result


if __name__ == "__main__":
    print("Example 1: Simple email")
    # example_simple()
    
    print("\nExample 2: Email with CC and BCC")
    # example_with_cc_bcc()
    
    print("\nExample 3: Multiple recipients")
    # example_multiple_recipients()
    
    print("\nNote: Uncomment the function calls above to actually send emails.")
    print("Make sure COOKIE_STRING is set in your .env file first!")
