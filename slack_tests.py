-- tests/test_duplicates_in_model.sql
-- Replace 'column_names' with the columns that should be unique together
-- Replace 'your_model_name' with your actual model name

{% test duplicate_check(model, column_names) %}

with grouped as (
    select
        {% for column in column_names %}
        {{ column }}{% if not loop.last %},{% endif %}
        {% endfor %},
        count(*) as num_records
    from {{ model }}
    group by 
        {% for column in column_names %}
        {{ column }}{% if not loop.last %},{% endif %}
        {% endfor %}
),

duplicates as (
    select *
    from grouped
    where num_records > 1
)

select * from duplicates

{% endtest %}


# models/schema.yml
version: 2

models:
  - name: your_model_name
    tests:
      - duplicate_check:
          column_names: 
            - id  # Replace with your unique column(s)
            # - column2  # Add more columns if needed
    columns:
      - name: id
        description: "Primary key"
        tests:
          - not_null

#!/usr/bin/env python
import os
import sys
import json
import requests
import subprocess
from datetime import datetime

# Slack webhook URL (store this in environment variables or dbt profiles)
SLACK_WEBHOOK_URL = os.environ.get('SLACK_WEBHOOK_URL')

# Function to run dbt test and capture results
def run_dbt_test(model_name):
    command = f"dbt test --select {model_name}"
    
    try:
        result = subprocess.run(
            command,
            shell=True,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        return True, result.stdout
    except subprocess.CalledProcessError as e:
        return False, e.stderr

# Function to check if duplicates were found
def check_for_duplicates(test_output):
    # This depends on your dbt output format, adjust as needed
    return "duplicate_check" in test_output and "FAIL" in test_output

# Function to get duplicate details (optional, for more detailed alerts)
def get_duplicate_details(model_name):
    # Run a query to get the actual duplicates
    # This is a simplified example - you'll need to customize based on your setup
    query_command = f"dbt run-operation get_duplicate_records --args \'{{'model_name': '{model_name}'}}\'"
    
    try:
        result = subprocess.run(
            query_command, 
            shell=True, 
            check=True,
            stdout=subprocess.PIPE,
            text=True
        )
        return result.stdout
    except:
        return "Could not retrieve duplicate details."

# Function to send Slack alert
def send_slack_alert(model_name, details=None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    message = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "⚠️ Duplicate Records Detected!",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Model:* `{model_name}`\n*Time:* {now}"
                }
            },
            {
                "type": "divider"
            }
        ]
    }
    
    if details:
        message["blocks"].append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Duplicate Details:*\n```{details}```"
            }
        })
    
    response = requests.post(
        SLACK_WEBHOOK_URL,
        data=json.dumps(message),
        headers={"Content-Type": "application/json"}
    )
    
    if response.status_code != 200:
        print(f"Error sending Slack alert: {response.text}")

# Main function
def main():
    if len(sys.argv) < 2:
        print("Usage: python dbt_duplicate_alert.py [model_name]")
        sys.exit(1)
    
    model_name = sys.argv[1]
    success, output = run_dbt_test(model_name)
    
    if not success and check_for_duplicates(output):
        print("Duplicates found! Sending alert...")
        details = get_duplicate_details(model_name)
        send_slack_alert(model_name, details)
    else:
        print("No duplicates found.")

if __name__ == "__main__":
    main()


# dbt_project.yml

name: 'your_project_name'
version: '1.0.0'
config-version: 2

# Add on-run-failure hook to your dbt project
on-run-failure:
  - "{{ slack_alert_on_failure(results) }}"

# Other project configurations
# ...

# Define your target path
target-path: "target"
clean-targets:
  - "target"
  - "dbt_modules"
  - "logs"

