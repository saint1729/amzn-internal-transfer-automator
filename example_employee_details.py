#!/usr/bin/env python
"""Example usage of the get_employee_details function.

This demonstrates how to use get_employee_details.py as a library function
instead of just running it as a CLI script.
"""

import logging
from get_employee_details import get_employee_details, get_employee_hierarchy

# Optional: Configure logging level for library use
# logging.basicConfig(level=logging.INFO)  # Show INFO logs
# logging.basicConfig(level=logging.DEBUG)  # Show DEBUG logs
logging.basicConfig(level=logging.WARNING)  # Only show WARNING+ (default for examples)


def example_single_employee():
    """Fetch details for a single employee."""
    username = "mrkcath"
    
    details = get_employee_details(username)
    
    if details:
        print(f"\n✓ Successfully retrieved details for {username}")
        print(f"  Name: {details['firstName']} {details['lastName']}")
        print(f"  Title: {details['businessTitle']}")
        print(f"  Level: {details['jobLevel']}")
        print(f"  Email: {details['primaryEmail']}")
        print(f"  Manager: {details.get('managerEmployeeIds', {}).get('login', 'N/A')}")
        print(f"  Location: {details['workLocation']['city']}, {details['workLocation']['country']}")
        print(f"  Cost Center: {details['costCenterName']}")
        print(f"  Is Manager: {details['isManager']}")
        print(f"  Tenure (days): {details['tenureDays']}")
    else:
        print(f"✗ Failed to retrieve details for {username}")
    
    return details


def example_employee_hierarchy():
    """Fetch employee hierarchy up to target level (e.g., L8)."""
    username = "saintamz"
    target_level = 8  # Fetch until we find an L8
    
    print(f"\nFetching hierarchy for {username} up to L{target_level}...")
    hierarchy = get_employee_hierarchy(username, target_level=target_level)
    
    if hierarchy:
        print(f"\n✓ Successfully retrieved hierarchy (found {len(hierarchy)} levels)")
        print(f"\nHierarchy tuples: {hierarchy}")
        print("\nFormatted hierarchy:")
        
        level_names = ["Employee", "Manager", "Skip Manager", "Skip+1", "Skip+2"]
        for i, (alias, firstname, lastname, job_level) in enumerate(hierarchy):
            level_name = level_names[i] if i < len(level_names) else f"Level {i}"
            
            if alias:
                print(f"  {level_name}: {firstname} {lastname} ({alias}) - L{job_level}")
            else:
                print(f"  {level_name}: (Not available)")
    else:
        print(f"✗ Failed to retrieve hierarchy for {username}")
    
    return hierarchy


def example_extended_hierarchy():
    """Fetch hierarchy with different target levels."""
    username = "saintamz"
    
    # Example 1: Get up to L7
    print(f"\nFetching hierarchy for {username} up to L7...")
    hierarchy_l7 = get_employee_hierarchy(username, target_level=7)
    print(f"Found {len(hierarchy_l7)} levels to reach L7")
    
    # Example 2: Get up to L10
    print(f"\nFetching hierarchy for {username} up to L10...")
    hierarchy_l10 = get_employee_hierarchy(username, target_level=10)
    print(f"Found {len(hierarchy_l10)} levels (stopped at highest available)")
    
    if hierarchy_l7:
        print("\nL7 Hierarchy:")
        for i, (alias, firstname, lastname, job_level) in enumerate(hierarchy_l7):
            if alias:
                print(f"  {i}: {firstname} {lastname} ({alias}) - L{job_level}")
        
        # Access specific levels
        employee = hierarchy_l7[0] if len(hierarchy_l7) > 0 else (None, None, None, None)
        print(f"\nEmployee: {employee[1]} {employee[2]} ({employee[0]}) is at level L{employee[3]}")
        
        if len(hierarchy_l7) > 1:
            manager = hierarchy_l7[1]
            print(f"Their manager: {manager[1]} {manager[2]} ({manager[0]}) is at level L{manager[3]}")
    
    return {"l7": hierarchy_l7, "l10": hierarchy_l10}


def example_multiple_employees():
    """Fetch details for multiple employees and compare them."""
    usernames = ["mrkcath", "samuelng"]  # Example usernames
    
    employees = {}
    for username in usernames:
        print(f"\nFetching {username}...")
        details = get_employee_details(username)
        if details:
            employees[username] = details
    
    if employees:
        print("\n" + "=" * 80)
        print("SUMMARY")
        print("=" * 80)
        for username, details in employees.items():
            print(f"\n{username}:")
            print(f"  {details['firstName']} {details['lastName']} - {details['businessTitle']}")
            print(f"  L{details['jobLevel']} | {details['workLocation']['city']}")
            print(f"  Manager: {details.get('managerEmployeeIds', {}).get('login', 'N/A')}")
    
    return employees


if __name__ == "__main__":
    print("=" * 80)
    print("Example 1: Fetch single employee details")
    print("=" * 80)
    # example_single_employee()
    
    print("\n\n" + "=" * 80)
    print("Example 2: Fetch employee hierarchy up to L8")
    print("=" * 80)
    # example_employee_hierarchy()
    
    print("\n\n" + "=" * 80)
    print("Example 3: Fetch hierarchy with different target levels")
    print("=" * 80)
    # example_extended_hierarchy()
    
    print("\n\n" + "=" * 80)
    print("Example 4: Fetch multiple employees")
    print("=" * 80)
    # example_multiple_employees()
    
    print("\n\nNote: Uncomment the function calls above to actually fetch employee details.")
    print("Make sure COGNITO_REFRESH_TOKEN is set in your .env file first!")
