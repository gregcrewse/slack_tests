#!/usr/bin/env python3
import os
import time
import json
import requests
from datetime import datetime, timedelta
import logging
import yaml

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("gitlab_comment_tracker.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("GitLabCommentTracker")

class GitLabCommentTracker:
    def __init__(self, config_path="config.yaml"):
        self.config = self.load_config(config_path)
        self.gitlab_token = self.config.get("gitlab_token") or os.environ.get("GITLAB_TOKEN")
        self.slack_token = self.config.get("slack_token") or os.environ.get("SLACK_TOKEN")
        
        if not self.gitlab_token or not self.slack_token:
            raise ValueError("GitLab token and Slack token must be provided")
        
        self.gitlab_url = self.config.get("gitlab_url", "https://gitlab.com")
        self.gitlab_api = f"{self.gitlab_url}/api/v4"
        self.tracked_users = self.config.get("tracked_users", [])
        self.tracked_projects = self.config.get("tracked_projects", [])
        self.slack_user_map = self.config.get("slack_user_map", {})
        self.db_file = self.config.get("db_file", "comment_tracker.json")
        self.check_interval = self.config.get("check_interval", 300)  # seconds
        
        # Load previously tracked comments
        self.tracked_comments = self.load_tracked_comments()
    
    def load_config(self, config_path):
        try:
            with open(config_path, "r") as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.error(f"Error loading config file: {e}")
            return {}
    
    def load_tracked_comments(self):
        try:
            if os.path.exists(self.db_file):
                with open(self.db_file, "r") as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"Error loading tracked comments: {e}")
            return {}
    
    def save_tracked_comments(self):
        try:
            with open(self.db_file, "w") as f:
                json.dump(self.tracked_comments, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving tracked comments: {e}")
    
    def get_gitlab_headers(self):
        return {"Private-Token": self.gitlab_token}
    
    def get_projects(self):
        """Get all projects the GitLab user has access to"""
        if self.tracked_projects:
            return self.tracked_projects
        
        projects = []
        page = 1
        
        while True:
            response = requests.get(
                f"{self.gitlab_api}/projects?membership=true&per_page=100&page={page}",
                headers=self.get_gitlab_headers()
            )
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch projects: {response.text}")
                break
            
            data = response.json()
            if not data:
                break
            
            projects.extend([{"id": p["id"], "path_with_namespace": p["path_with_namespace"]} for p in data])
            page += 1
        
        return projects
    
    def get_merge_requests(self, project_id):
        """Get open merge requests for a project"""
        response = requests.get(
            f"{self.gitlab_api}/projects/{project_id}/merge_requests?state=opened",
            headers=self.get_gitlab_headers()
        )
        
        if response.status_code != 200:
            logger.error(f"Failed to fetch MRs for project {project_id}: {response.text}")
            return []
        
        return response.json()
    
    def get_merge_request_details(self, project_id, mr_iid):
        """Get details of a specific merge request"""
        response = requests.get(
            f"{self.gitlab_api}/projects/{project_id}/merge_requests/{mr_iid}",
            headers=self.get_gitlab_headers()
        )
        
        if response.status_code != 200:
            logger.error(f"Failed to fetch MR details for project {project_id}, MR {mr_iid}: {response.text}")
            return None
        
        return response.json()
    
    def get_discussions(self, project_id, mr_iid):
        """Get all discussions (comments) on a merge request"""
        response = requests.get(
            f"{self.gitlab_api}/projects/{project_id}/merge_requests/{mr_iid}/discussions",
            headers=self.get_gitlab_headers()
        )
        
        if response.status_code != 200:
            logger.error(f"Failed to fetch discussions for project {project_id}, MR {mr_iid}: {response.text}")
            return []
        
        return response.json()
    
    def get_user_info(self, user_id):
        """Get user information from GitLab"""
        response = requests.get(
            f"{self.gitlab_api}/users/{user_id}",
            headers=self.get_gitlab_headers()
        )
        
        if response.status_code != 200:
            logger.error(f"Failed to fetch user info for {user_id}: {response.text}")
            return None
        
        return response.json()
    
    def should_track_user(self, username):
        """Check if a user should be tracked"""
        if not self.tracked_users:  # If empty, track all users
            return True
        return username in self.tracked_users
    
    def get_slack_user_id(self, gitlab_username):
        """Get Slack user ID from GitLab username using the mapping"""
        return self.slack_user_map.get(gitlab_username)
    
    def send_slack_message(self, user_id, message):
        """Send a message to a user on Slack"""
        if not user_id:
            logger.warning(f"No Slack user ID found for GitLab user, message: {message}")
            return False
        
        try:
            response = requests.post(
                "https://slack.com/api/chat.postMessage",
                headers={
                    "Authorization": f"Bearer {self.slack_token}",
                    "Content-Type": "application/json"
                },
                json={
                    "channel": user_id,
                    "text": message,
                    "link_names": True,
                    "parse": "full"
                }
            )
            
            if response.status_code != 200 or not response.json().get("ok"):
                logger.error(f"Failed to send Slack message: {response.text}")
                return False
            
            return True
        except Exception as e:
            logger.error(f"Error sending Slack message: {e}")
            return False
    
    def format_comment_notification(self, project, mr, comment, is_followup=False):
        """Format a Slack notification about a comment"""
        comment_url = f"{self.gitlab_url}/{project['path_with_namespace']}/-/merge_requests/{mr['iid']}#note_{comment['id']}"
        
        if is_followup:
            return (
                f"⏰ *Reminder:* A comment on your merge request still needs a response\n"
                f"*Project:* {project['path_with_namespace']}\n"
                f"*MR:* {mr['title']}\n"
                f"*Comment by:* {comment['author']['username']}\n"
                f"*Posted:* {comment['created_at']}\n"
                f"*URL:* {comment_url}\n\n"
                f"```{comment['body']}```"
            )
        else:
            return (
                f"💬 *New comment on a merge request*\n"
                f"*Project:* {project['path_with_namespace']}\n"
                f"*MR:* {mr['title']}\n"
                f"*Comment by:* {comment['author']['username']}\n"
                f"*URL:* {comment_url}\n\n"
                f"```{comment['body']}```"
            )
    
    def check_for_new_comments(self):
        """Check for new comments on merge requests and send notifications"""
        projects = self.get_projects()
        current_time = datetime.now().isoformat()
        
        for project in projects:
            project_id = project['id']
            logger.info(f"Checking project: {project['path_with_namespace']} (ID: {project_id})")
            
            mrs = self.get_merge_requests(project_id)
            for mr in mrs:
                mr_iid = mr['iid']
                mr_author = mr['author']['username']
                mr_details = self.get_merge_request_details(project_id, mr_iid)
                
                if not mr_details:
                    continue
                
                # Get reviewers - both assigned reviewers and those who've left comments
                reviewers = set()
                if 'reviewers' in mr_details and mr_details['reviewers']:
                    reviewers.update(r['username'] for r in mr_details['reviewers'])
                
                discussions = self.get_discussions(project_id, mr_iid)
                
                # Process each discussion
                for discussion in discussions:
                    for note in discussion.get('notes', []):
                        # Skip system notes
                        if note.get('system', False):
                            continue
                        
                        note_id = str(note['id'])
                        note_author = note['author']['username']
                        
                        # Add comment author to reviewers if they're not the MR author
                        if note_author != mr_author:
                            reviewers.add(note_author)
                        
                        # Check if we should track this note's author
                        if not self.should_track_user(note_author):
                            continue
                        
                        # Check if this is a new comment
                        is_new_comment = False
                        if note_id not in self.tracked_comments:
                            is_new_comment = True
                            self.tracked_comments[note_id] = {
                                "project_id": project_id,
                                "mr_iid": mr_iid,
                                "author": note_author,
                                "created_at": note['created_at'],
                                "notified_at": current_time,
                                "responded": False,
                                "followup_sent": False
                            }
                        
                        # Check if comment needs a followup reminder
                        needs_followup = False
                        if (
                            note_id in self.tracked_comments and
                            not self.tracked_comments[note_id]["responded"] and
                            not self.tracked_comments[note_id]["followup_sent"]
                        ):
                            created_at = datetime.fromisoformat(self.tracked_comments[note_id]["created_at"].replace('Z', '+00:00'))
                            if datetime.now() - created_at > timedelta(hours=24):
                                needs_followup = True
                                self.tracked_comments[note_id]["followup_sent"] = True
                        
                        # Check if a response has been added for this comment
                        for response_note in discussion.get('notes', []):
                            if response_note.get('system', False):
                                continue
                            
                            response_author = response_note['author']['username']
                            response_created_at = datetime.fromisoformat(response_note['created_at'].replace('Z', '+00:00'))
                            note_created_at = datetime.fromisoformat(note['created_at'].replace('Z', '+00:00'))
                            
                            # If a different user responded after this comment was created
                            if (
                                response_author != note_author and
                                response_created_at > note_created_at and
                                note_id in self.tracked_comments and
                                not self.tracked_comments[note_id]["responded"]
                            ):
                                self.tracked_comments[note_id]["responded"] = True
                                self.tracked_comments[note_id]["responder"] = response_author
                                
                                # If the MR author responded to a reviewer's comment, notify the reviewer
                                if (
                                    response_author == mr_author and
                                    note_author in reviewers and
                                    self.should_track_user(note_author)
                                ):
                                    slack_user = self.get_slack_user_id(note_author)
                                    if slack_user:
                                        message = (
                                            f"✅ *MR author responded to your comment*\n"
                                            f"*Project:* {project['path_with_namespace']}\n"
                                            f"*MR:* {mr['title']}\n"
                                            f"*Response by:* {response_author}\n"
                                            f"*URL:* {self.gitlab_url}/{project['path_with_namespace']}/-/merge_requests/{mr_iid}#note_{response_note['id']}"
                                        )
                                        self.send_slack_message(slack_user, message)
                        
                        # Send notifications for new comments or follow-ups
                        if is_new_comment or needs_followup:
                            # If comment is from a reviewer, notify the MR author
                            if note_author != mr_author and self.should_track_user(mr_author):
                                slack_user = self.get_slack_user_id(mr_author)
                                if slack_user:
                                    message = self.format_comment_notification(
                                        project, mr, note, is_followup=needs_followup
                                    )
                                    self.send_slack_message(slack_user, message)
                
                # Save changes to tracked comments
                self.save_tracked_comments()
    
    def run(self):
        """Run the comment tracker continuously"""
        logger.info("Starting GitLab Comment Tracker")
        
        while True:
            try:
                self.check_for_new_comments()
                logger.info(f"Sleeping for {self.check_interval} seconds")
                time.sleep(self.check_interval)
            except KeyboardInterrupt:
                logger.info("Stopping GitLab Comment Tracker")
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time.sleep(60)  # Wait a bit before trying again

if __name__ == "__main__":
    tracker = GitLabCommentTracker()
    tracker.run()

# GitLab Comment Tracker Configuration

# API Tokens (can also be set as environment variables)
# gitlab_token: "your_gitlab_personal_access_token" 
# slack_token: "your_slack_bot_token"

# GitLab instance URL (default is gitlab.com)
gitlab_url: "https://gitlab.com"

# Users to track (leave empty to track all users)
# Only these users will trigger notifications
tracked_users:
  - "user1"
  - "user2"
  - "user3"

# Projects to monitor (leave empty to check all accessible projects)
# Format: either project IDs or full path with namespace
tracked_projects:
  - { id: 12345, path_with_namespace: "group/project-name" }
  - { id: 67890, path_with_namespace: "group/another-project" }

# Map GitLab usernames to Slack user IDs
slack_user_map:
  "user1": "U012ABC3DEF"
  "user2": "U345GHI6JKL"
  "user3": "U789MNO0PQR"

# Storage settings
db_file: "comment_tracker.json"

# Check interval in seconds (default: 300 = 5 minutes)
check_interval: 300


#!/bin/bash

echo "Setting up GitLab Comment Tracker..."

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install requests PyYAML

# Create config file if it doesn't exist
if [ ! -f "config.yaml" ]; then
    cp config.yaml.template config.yaml
    echo "Created config.yaml from template. Please edit this file with your settings."
else
    echo "Using existing config.yaml"
fi

# Set up cron job to run the script
read -p "Would you like to set up a cron job to run this script automatically? (y/n): " setup_cron

if [ "$setup_cron" = "y" ]; then
    # Get the absolute path of the current directory
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    
    # Create a wrapper script
    cat > "$SCRIPT_DIR/run_tracker.sh" << EOL
#!/bin/bash
cd "$SCRIPT_DIR"
source venv/bin/activate
python3 gitlab_comment_tracker.py
EOL
    
    chmod +x "$SCRIPT_DIR/run_tracker.sh"
    
    # Add to crontab to run every 5 minutes
    (crontab -l 2>/dev/null; echo "*/5 * * * * $SCRIPT_DIR/run_tracker.sh") | crontab -
    
    echo "Cron job set up to run every 5 minutes"
fi

echo "Setup complete!"
echo "Next steps:"
echo "1. Edit config.yaml with your GitLab and Slack tokens"
echo "2. Update the tracked users and projects in config.yaml"
echo "3. Add GitLab username to Slack user ID mappings in config.yaml"
echo "4. Run 'python3 gitlab_comment_tracker.py' to start the tracker"

GitLab Comment Tracker with Slack Alerts
This tool tracks comments on GitLab merge requests and sends Slack notifications to relevant team members. It helps teams stay on top of code review discussions by:

Alerting MR authors when someone comments on their merge request
Notifying reviewers when the MR author responds to their comments
Sending follow-up reminders when comments haven't been addressed within 24 hours

Features

Targeted notifications: Only sends alerts to relevant team members (MR authors and reviewers)
User filtering: Can be configured to track only specific GitLab users
Project filtering: Can focus on specific repositories or projects
Follow-up reminders: Sends reminders after 24 hours if comments haven't been addressed
Response tracking: Detects when comments have been responded to
Continuous monitoring: Runs as a background service checking for new comments regularly

Setup
Prerequisites

Python 3.6+
GitLab personal access token with API access
Slack Bot token with permissions to send messages

Installation

Clone this repository:
Copygit clone <repository-url>
cd gitlab-comment-tracker

Run the setup script:
Copychmod +x setup.sh
./setup.sh

Edit the configuration file:
Copynano config.yaml


Configuration
The config.yaml file contains all the settings needed to run the tracker:
API Tokens
yamlCopygitlab_token: "your_gitlab_personal_access_token"
slack_token: "your_slack_bot_token"
You can also set these as environment variables:
Copyexport GITLAB_TOKEN="your_gitlab_personal_access_token"
export SLACK_TOKEN="your_slack_bot_token"
GitLab URL
yamlCopygitlab_url: "https://gitlab.com"
Change this if you're using a self-hosted GitLab instance.
Users to Track
yamlCopytracked_users:
  - "user1"
  - "user2"
  - "user3"
Leave this empty to track all users.
Projects to Monitor
yamlCopytracked_projects:
  - { id: 12345, path_with_namespace: "group/project-name" }
  - { id: 67890, path_with_namespace: "group/another-project" }
Leave this empty to check all accessible projects.
GitLab to Slack User Mapping
yamlCopyslack_user_map:
  "gitlab_username1": "SLACK_USER_ID1"
  "gitlab_username2": "SLACK_USER_ID2"
You need to map GitLab usernames to Slack user IDs to enable notifications.
Other Settings
yamlCopydb_file: "comment_tracker.json"  # Local storage file
check_interval: 300              # Check frequency in seconds (5 minutes)
Getting Slack User IDs
To get a Slack user ID:

Open Slack in a web browser
Go to a user's profile
Click "More" and select "Copy member ID"

Required GitLab Token Permissions
Your GitLab personal access token needs these scopes:

api (Full API access)
read_api (if you prefer more limited access)

Required Slack Bot Permissions
Your Slack app needs these OAuth scopes:

chat:write (Send messages)
im:write (Send direct messages)

Running the Tracker
Manually
Copypython3 gitlab_comment_tracker.py
As a Service (Linux)
Create a systemd service:
Copysudo nano /etc/systemd/system/gitlab-comment-tracker.service
Add the following content:
Copy[Unit]
Description=GitLab Comment Tracker
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/gitlab-comment-tracker
ExecStart=/path/to/gitlab-comment-tracker/venv/bin/python /path/to/gitlab-comment-tracker/gitlab_comment_tracker.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
Enable and start the service:
Copysudo systemctl enable gitlab-comment-tracker
sudo systemctl start gitlab-comment-tracker
Notification Examples
New Comment on Your MR
Copy💬 *New comment on a merge request*
*Project:* group/project-name
*MR:* Fix authentication bug
*Comment by:* reviewer1
*URL:* https://gitlab.com/group/project-name/-/merge_requests/123#note_456789
This is a critical bug that needs to be fixed before merging. The authentication flow is broken.
Copy
Author Response to Your Comment
Copy✅ *MR author responded to your comment*
*Project:* group/project-name
*MR:* Fix authentication bug
*Response by:* author1
*URL:* https://gitlab.com/group/project-name/-/merge_requests/123#note_456790
Follow-up Reminder
Copy⏰ *Reminder:* A comment on your merge request still needs a response
*Project:* group/project-name
*MR:* Fix authentication bug
*Comment by:* reviewer1
*Posted:* 2023-04-01T14:30:45Z
*URL:* https://gitlab.com/group/project-name/-/merge_requests/123#note_456789
This is a critical bug that needs to be fixed before merging. The authentication flow is broken.
Copy
Troubleshooting
Check the Logs
Look at gitlab_comment_tracker.log for error messages and debugging information.
Common Issues

API Limit Reached: Increase the check_interval in the config.
Authentication Errors: Verify your GitLab and Slack tokens.
Notifications Not Sending: Check the Slack user ID mappings.

Contributing
Contributions are welcome! Please feel free to submit a Pull Request.
License
This project is licensed under the MIT License - see the LICENSE file for details.
