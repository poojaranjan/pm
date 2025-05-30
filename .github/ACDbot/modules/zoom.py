import requests
import os
from datetime import datetime, timedelta, timezone
import json
import urllib.parse
import calendar

account_id=os.environ.get("ZOOM_ACCOUNT_ID", "")
client_id=os.environ["ZOOM_CLIENT_ID"]
client_secret=os.environ["ZOOM_CLIENT_SECRET"]
refresh_token=os.environ.get("ZOOM_REFRESH_TOKEN", "")

auth_token_url = "https://zoom.us/oauth/token"
api_base_url = "https://api.zoom.us/v2"

def create_meeting(topic, start_time, duration):

    access_token = get_access_token()

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    # Get alternative hosts from environment
    alternative_hosts = os.environ.get("ZOOM_ALTERNATIVE_HOSTS", "")
    
    payload = {
        "topic": topic,
        "type": 2,  # Scheduled meeting
        "start_time": start_time,  # ISO 8601 format, e.g., "2025-01-18T14:00:00Z"
        "duration": duration,  # Duration in minutes
        "settings": {
            "auto_start_meeting_summary": True,
            "auto_start_ai_companion_questions": True,
            "join_before_host": True,  
            #"waiting_room": True,
            "meeting_authentication": False,
            "auto_recording": "cloud",  
            "approval_type": 2,  
            "alternative_hosts": alternative_hosts,  
            "recording": {
                "auto_recording": "cloud",
                "cloud_recording_download": True,
                "cloud_recording_thumbnails": True,
                "recording_audio_transcript": True, 
            },
        }
    }
    resp = requests.post(f"{api_base_url}/users/me/meetings", 
                            headers=headers, 
                            json=payload)
    
    if resp.status_code!=201:
        print("Unable to generate meeting link")
        resp.raise_for_status()
    response_data = resp.json()
    
    content = {
                "meeting_url": response_data["join_url"], 
                "password": response_data.get("password", ""),
                "meetingTime": response_data["start_time"],
                "purpose": response_data["topic"],
                "duration": response_data["duration"],
                "message": "Success",
                "status":1
    }
    print(content)
    return response_data["join_url"], response_data["id"]

def get_access_token():
    """
    Get an access token using the refresh token (OAuth 2.0) for a General (User Managed) app
    instead of account_credentials used for Server-to-Server apps.
    """
    global refresh_token
    
    if not refresh_token:
        raise ValueError("ZOOM_REFRESH_TOKEN environment variable is required for User Managed apps")
        
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }
    
    response = requests.post(auth_token_url, 
                             auth=(client_id, client_secret), 
                             data=data)
    
    if response.status_code != 200:
        print("Unable to get access token")
        response.raise_for_status()
    else:
        response_data = response.json()
        
        # If the response includes a new refresh token, update it in memory
        if "refresh_token" in response_data:
            new_refresh_token = response_data["refresh_token"]
            # Update the global refresh_token variable
            refresh_token = new_refresh_token
            # Update the environment variable for other processes to use
            os.environ["ZOOM_REFRESH_TOKEN"] = new_refresh_token
            print("Received new refresh token - token hidden for security")
            print("IMPORTANT: Updated ZOOM_REFRESH_TOKEN variable with the new value")
            
            # Save to a temporary file in a shared location that can be read by other workflow steps
            try:
                token_dir = os.path.join(os.getcwd(), ".github", "ACDbot", "tokens")
                os.makedirs(token_dir, exist_ok=True)
                token_file = os.path.join(token_dir, "zoom_new_refresh_token.txt")
                with open(token_file, "w") as f:
                    f.write(new_refresh_token)
                print(f"New refresh token saved to {token_file} for GitHub Actions update")
            except Exception as e:
                print(f"Warning: Failed to save new refresh token to file: {str(e)}")
            
        return response_data["access_token"]

def get_meeting_recording(meeting_id):
    access_token = get_access_token()
    headers = {
        "Authorization": f"Bearer {access_token}"
    }
    # URL-encode the meeting id to ensure a compliant endpoint URL.
    meeting_id_encoded = urllib.parse.quote(str(meeting_id), safe='')
    url = f"{api_base_url}/meetings/{meeting_id_encoded}/recordings"

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        error_details = response.json()
        print(f"Error fetching meeting recording: {response.status_code} {response.reason} - {error_details}")
        return None

    return response.json()

def get_meeting_transcript(meeting_id):
    """
    Fetches the transcript file content for a given meeting ID.

    :param meeting_id: The Zoom meeting ID
    :return: Transcript text content
    """
    access_token = get_access_token()
    headers = {
        "Authorization": f"Bearer {access_token}"
    }
    url = f"{api_base_url}/meetings/{meeting_id}/recordings"
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        print(f"Error fetching meeting recordings: {response.status_code} {response.text}")
        response.raise_for_status()
    data = response.json()
    recording_files = data.get('recording_files', [])

    # Find the transcript file
    transcript_file = None
    for file in recording_files:
        if file.get('file_type') == 'TRANSCRIPT':
            transcript_file = file
            break

    if not transcript_file:
        print(f"No transcript found for meeting {meeting_id} - available files: {recording_files}")
        return None

    download_url = transcript_file.get('download_url')
    if not download_url:
        raise ValueError("Transcript download URL not found.")

    # Download the transcript file
    transcript_content = download_zoom_file(download_url, access_token)
    return transcript_content

def download_zoom_file(download_url, access_token):
    """
    Downloads a file from Zoom using the access token.

    :param download_url: The URL to the file
    :param access_token: Zoom access token
    :return: Content of the file
    """
    response = requests.get(download_url, headers={"Authorization": f"Bearer {access_token}"})
    if response.status_code != 200:
        print(f"Error downloading file: {response.status_code} {response.text}")
        response.raise_for_status()
    return response.content.decode('utf-8')

def get_recordings_list(from_date=None, to_date=None):
    """
    Retrieves a list of cloud recordings for the user, handling pagination.
    Args:
        from_date (str, optional): Start date in YYYY-MM-DD format. Defaults to 7 days ago.
        to_date (str, optional): End date in YYYY-MM-DD format. Defaults to today.
    Returns:
        list: A list of meeting recording objects.
    """
    access_token = get_access_token()
    headers = {
        "Authorization": f"Bearer {access_token}"
    }
    
    # Set default date range if not provided
    if from_date is None:
        from_date = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
    if to_date is None:
        to_date = datetime.utcnow().strftime("%Y-%m-%d")
        
    print(f"[DEBUG] Fetching recordings from {from_date} to {to_date}")

    params = {
        "page_size": 300, # Max page size allowed by Zoom
        "from": from_date,
        "to": to_date,
        "next_page_token": None # Start without a token
    }
    
    all_recordings = []
    page_number = 1
    
    while True:
        if params["next_page_token"]:
             print(f"[DEBUG] Fetching page {page_number} with next_page_token...")
        else:
             print(f"[DEBUG] Fetching page {page_number}...")
             # Don't send None as a parameter value
             current_params = {k: v for k, v in params.items() if k != "next_page_token" or v is not None}

        try:
            response = requests.get(f"{api_base_url}/users/me/recordings", headers=headers, params=current_params)
            response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
            data = response.json()
        except requests.exceptions.RequestException as e:
            print(f"::error::Error fetching recordings (Page {page_number}): {e}")
            # Decide whether to break or continue; breaking is safer
            break 
        except json.JSONDecodeError as e:
            print(f"::error::Error decoding JSON response (Page {page_number}): {e}")
            break

        page_recordings = data.get("meetings", [])
        all_recordings.extend(page_recordings)
        print(f"[DEBUG] Fetched {len(page_recordings)} recordings on page {page_number}. Total fetched: {len(all_recordings)}.")

        next_page_token = data.get("next_page_token")
        params["next_page_token"] = next_page_token
        
        # Exit loop if no next_page_token is provided or it's an empty string
        if not next_page_token:
            print("[DEBUG] No next_page_token found. Finished fetching recordings.")
            break
            
        page_number += 1
        # Add a small delay to avoid hitting rate limits, if necessary
        # time.sleep(0.2) 

    print(f"[DEBUG] Total recordings fetched across all pages: {len(all_recordings)}")
    return all_recordings

def get_meeting_summary(meeting_uuid: str) -> dict:
    """Temporary workaround for summary endpoint"""
    try:
        # First try with server-to-server token
        access_token = get_access_token()
        headers = {"Authorization": f"Bearer {access_token}"}
        encoded_uuid = requests.utils.quote(meeting_uuid, safe='')
        
        print(f"Attempting summary with UUID: {encoded_uuid}")  # Debug
        
        response = requests.get(
            f"https://api.zoom.us/v2/meetings/{encoded_uuid}/meeting_summary",
            headers=headers
        )
        
        print(f"API Response: {response.status_code}")  # Debug
        
        if response.status_code == 404:
            print("Zoom API: Summary not found")
            return {}
            
        response.raise_for_status()
        summary = response.json()
        print(f"Raw summary data: {json.dumps(summary, indent=2)}")  # Debug
        return summary
        
    except requests.HTTPError as e:
        print(f"Zoom API Error ({e.response.status_code}): {e.response.text}")
        return {}
    except Exception as e:
        print(f"General error: {str(e)}")
        return {}
def get_meeting(meeting_id):
    """
    Retrieves details for a specific Zoom meeting.
    Returns: dict with meeting details including 'join_url'
    """
    access_token = get_access_token()
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    get_url = f"{api_base_url}/meetings/{meeting_id}"
    response = requests.get(get_url, headers=headers)
    response.raise_for_status()
    
    return response.json()

def update_meeting(meeting_id, topic, start_time, duration):
    """
    Updates an existing Zoom meeting using the PATCH method.
    See Zoom API documentation: https://developers.zoom.us/docs/api/meetings/#tag/meetings/PATCH/meetings/{meetingId}
    
    :param meeting_id: Zoom meeting ID to update.
    :param topic: Updated meeting topic/title.
    :param start_time: Updated start time in ISO 8601 format (e.g., "2025-01-18T14:00:00Z").
    :param duration: Updated duration in minutes.
    :return: A dict confirming the update.
    """
    access_token = get_access_token()
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "topic": topic,
        "start_time": start_time,
        "duration": duration
    }
    update_url = f"{api_base_url}/meetings/{meeting_id}"
    resp = requests.patch(update_url, headers=headers, json=payload)
    
    if resp.status_code != 204:
        print(f"Error updating meeting {meeting_id}: {resp.status_code} {resp.text}")
        resp.raise_for_status()
    
    # Get updated meeting details to retrieve join_url
    meeting_details = get_meeting(meeting_id)
    
    return {
        "id": meeting_id,
        "join_url": meeting_details["join_url"],
        "message": "Meeting updated successfully"
    }

def create_recurring_meeting(topic, start_time, duration, occurrence_rate):
    """
    Creates a recurring Zoom meeting
    Args:
        topic: Meeting title
        start_time: Start time in ISO format
        duration: Duration in minutes
        occurrence_rate: weekly, bi-weekly, or monthly
    Returns:
        Tuple of (join_url, meeting_id)
    """
    access_token = get_access_token()

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    # Parse the start_time to get the day of week and ensure it's correctly formatted
    try:
        # Convert ISO 8601 string to datetime object
        from datetime import datetime, timedelta
        start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        
        # Calculate day_of_week for LOGGING/DEBUGGING (using Mon=1..Sun=7)
        log_day_of_week = start_dt.weekday() + 1 
        print(f"[DEBUG] Original start_time: {start_time}")
        print(f"[DEBUG] Parsed date: {start_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"[DEBUG] Calculated day of week for logging: {log_day_of_week} (Mon=1..Sun=7)")
        print(f"[DEBUG] Weekday name: {start_dt.strftime('%A')}")
        
        # Calculate day_of_week for Zoom API weekly_days (Sun=1..Sat=7)
        zoom_api_weekly_day = (start_dt.weekday() + 1) % 7 + 1
        print(f"[DEBUG] Calculated day of week for Zoom API: {zoom_api_weekly_day} (Sun=1..Sat=7)")

        # Map occurrence rate to Zoom recurrence type
        recurrence = {
            "type": 2,  # Default to weekly
            "repeat_interval": 1,
            "weekly_days": str(zoom_api_weekly_day), # Use Zoom API format
            "end_times": 12 # Default end time
        }
        
        if occurrence_rate == "weekly":
            recurrence["type"] = 2
            recurrence["repeat_interval"] = 1
            recurrence["weekly_days"] = str(zoom_api_weekly_day) # Use Zoom API format
            print(f"[DEBUG] Setting up weekly recurrence with day {zoom_api_weekly_day} (Sun=1..Sat=7)")
        elif occurrence_rate == "bi-weekly":
            recurrence["type"] = 2
            recurrence["repeat_interval"] = 2 # Correct interval for bi-weekly
            recurrence["weekly_days"] = str(zoom_api_weekly_day) # Use Zoom API format
            print(f"[DEBUG] Setting up bi-weekly recurrence every 2 weeks on day {zoom_api_weekly_day} (Sun=1..Sat=7)")
        elif occurrence_rate == "monthly":
            # According to Zoom API docs:
            # For monthly by week day (second Wednesday of each month):
            # type=3 (Monthly)
            # monthly_week: 1-4 or -1 for last week
            # monthly_week_day: 1-7 where 1=Sunday, 7=Saturday
            
            # Calculate which week of the month this date falls on (1-4 or -1 for last)
            day_of_month = start_dt.day
            week_number = (day_of_month - 1) // 7 + 1  # 1-based week number (1st, 2nd, 3rd, 4th)
            
            # If it's the last occurrence of this weekday in the month
            days_in_month = calendar.monthrange(start_dt.year, start_dt.month)[1]
            if day_of_month + 7 > days_in_month:
                # This is the last occurrence of this weekday in the month
                week_number = -1
            
            print(f"[DEBUG] Setting up monthly recurrence on the {week_number}{'st' if week_number == 1 else 'nd' if week_number == 2 else 'rd' if week_number == 3 else 'th'} {start_dt.strftime('%A')} of each month")
            
            # Remove weekly_days as it's not used for monthly meetings
            if "weekly_days" in recurrence:
                del recurrence["weekly_days"]
            
            # Convert from Zoom day format (1=Monday) to monthly_week_day format (1=Sunday, 7=Saturday)
            # In Zoom's API: 1=Sunday, 2=Monday, ..., 7=Saturday
            monthly_week_day_format = zoom_api_weekly_day # Use the correct Sun=1 mapping
            
            # Make sure week_number is within valid range (1-4, -1)
            if week_number > 4:
                week_number = 4
            
            # For monthly meetings by weekday, we need:
            recurrence["type"] = 3  # Monthly
            recurrence["repeat_interval"] = 1
            recurrence["monthly_week"] = week_number  # 1-4 or -1 for last week
            recurrence["monthly_week_day"] = monthly_week_day_format  # Ensure correct day format is used
            
            print(f"[DEBUG] Using type=3, repeat_interval=1, monthly_week={week_number}, monthly_week_day={monthly_week_day_format} (Sun=1..Sat=7) for day-of-week pattern")
            
            # We'll implement a fallback mechanism in handle_issue.py to add calendar events
            # that follow the same-weekday-of-month pattern even if Zoom doesn't
        
    except Exception as e:
        print(f"[DEBUG] Error calculating day of week details: {str(e)}")
        # Default recurrence if calculation fails (might lead to incorrect day)
        recurrence = {"type": 1, "repeat_interval": 1} # Default to Daily if error occurs
        zoom_api_weekly_day = 1 # Default fallback for safety
    
    # Remove the adjust_start_date_for_zoom function and its call
    # The start_time adjustment seems to cause more issues than it solves
    # formatted_start_time = adjust_start_date_for_zoom(start_dt, occurrence_rate, day_of_week)
    formatted_start_time = start_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    print(f"[DEBUG] Using original start time for API call: {formatted_start_time}")

    # Get alternative hosts from environment
    alternative_hosts = os.environ.get("ZOOM_ALTERNATIVE_HOSTS", "")
    
    # Construct payload with the recurrence settings
    payload = {
        "topic": topic,
        "type": 8,  # Recurring meeting with fixed time
        "start_time": formatted_start_time, # Use the formatted original start time
        "duration": duration,
        "recurrence": recurrence,
        "settings": {
            "auto_start_meeting_summary": True,
            "auto_start_ai_companion_questions": True,
            "join_before_host": True,  
            "meeting_authentication": False,
            "auto_recording": "cloud",  
            "approval_type": 2,  
            "alternative_hosts": alternative_hosts,  
            "recording": {
                "auto_recording": "cloud",
                "cloud_recording_download": True,
                "cloud_recording_thumbnails": True,
                "recording_audio_transcript": True, 
            },
        }
    }

    print(f"[DEBUG] Creating recurring Zoom meeting with payload: {json.dumps(payload, indent=2)}")
    
    try:
        resp = requests.post(f"{api_base_url}/users/me/meetings", 
                            headers=headers, 
                            json=payload)
        
        # Check response
        if resp.status_code == 201:
            response_data = resp.json()
            print(f"[DEBUG] Successfully created recurring meeting: {json.dumps(response_data, indent=2)}")
            
            # Check if the first occurrence matches our intended start date
            if 'occurrences' in response_data and response_data['occurrences']:
                first_occurrence = response_data['occurrences'][0]
                first_occurrence_time = first_occurrence.get('start_time')
                if first_occurrence_time:
                    print(f"[DEBUG] First occurrence scheduled for: {first_occurrence_time}")
                    first_occurrence_dt = datetime.fromisoformat(first_occurrence_time.replace('Z', '+00:00'))
                    
                    # Compare with original start_time
                    original_dt = start_dt
                    print(f"[DEBUG] Original start date: {original_dt.strftime('%Y-%m-%d')}")
                    print(f"[DEBUG] First occurrence date: {first_occurrence_dt.strftime('%Y-%m-%d')}")
                    
                    # If dates don't match, log a warning but continue
                    if original_dt.date() != first_occurrence_dt.date():
                        print(f"[WARNING] First occurrence date ({first_occurrence_dt.strftime('%Y-%m-%d')}) " 
                              f"does not match requested date ({original_dt.strftime('%Y-%m-%d')})")
                        print(f"[WARNING] This is a Zoom API behavior - please check the meeting details in Zoom")
                        
                        # Don't raise an error - the meeting was created successfully but on a different date
                        # We'll let the caller decide what to do with this information
            
            content = {
                "meeting_url": response_data["join_url"], 
                "password": response_data.get("password", ""),
                "meetingTime": response_data["start_time"],
                "purpose": response_data["topic"],
                "duration": response_data["duration"],
                "message": "Success",
                "status":1
            }
            print(content)
            return response_data["join_url"], response_data["id"]
            
        else:
            # For alternative host errors, try again without that field
            if resp.status_code == 400 and "Invalid email alternative_host" in resp.text:
                print(f"[DEBUG] Alternative host error detected, retrying without alternative hosts")
                # Remove alternative_hosts from settings
                if "alternative_hosts" in payload["settings"]:
                    del payload["settings"]["alternative_hosts"]
                
                print(f"[DEBUG] Retrying with modified payload: {json.dumps(payload, indent=2)}")
                
                # Try again
                resp = requests.post(f"{api_base_url}/users/me/meetings", 
                                    headers=headers, 
                                    json=payload)
                                    
                if resp.status_code == 201:
                    response_data = resp.json()
                    print(f"[DEBUG] Successfully created recurring meeting: {json.dumps(response_data, indent=2)}")
                    
                    # Check if the first occurrence matches our intended start date
                    if 'occurrences' in response_data and response_data['occurrences']:
                        first_occurrence = response_data['occurrences'][0]
                        first_occurrence_time = first_occurrence.get('start_time')
                        if first_occurrence_time:
                            print(f"[DEBUG] First occurrence scheduled for: {first_occurrence_time}")
                            first_occurrence_dt = datetime.fromisoformat(first_occurrence_time.replace('Z', '+00:00'))
                            
                            # Compare with original start_time
                            original_dt = start_dt
                            print(f"[DEBUG] Original start date: {original_dt.strftime('%Y-%m-%d')}")
                            print(f"[DEBUG] First occurrence date: {first_occurrence_dt.strftime('%Y-%m-%d')}")
                            
                            # If dates don't match, log a warning but continue
                            if original_dt.date() != first_occurrence_dt.date():
                                print(f"[WARNING] First occurrence date ({first_occurrence_dt.strftime('%Y-%m-%d')}) " 
                                      f"does not match requested date ({original_dt.strftime('%Y-%m-%d')})")
                                print(f"[WARNING] This is a Zoom API behavior - please check the meeting details in Zoom")
                                
                                # Don't raise an error - the meeting was created successfully but on a different date
                                # We'll let the caller decide what to do with this information
                    
                    content = {
                        "meeting_url": response_data["join_url"], 
                        "password": response_data.get("password", ""),
                        "meetingTime": response_data["start_time"],
                        "purpose": response_data["topic"],
                        "duration": response_data["duration"],
                        "message": "Success",
                        "status":1
                    }
                    print(content)
                    return response_data["join_url"], response_data["id"]
                else:
                    print(f"Unable to generate meeting link even without alternative hosts: {resp.text}")
                    resp.raise_for_status()
            else:
                print(f"Unable to generate meeting link: {resp.text}")
                resp.raise_for_status()
                
    except Exception as e:
        error_message = str(e)
        print(f"Error creating recurring Zoom meeting: {error_message}")
        
        # Check if there's a response body with more details
        if hasattr(e, 'response') and hasattr(e.response, 'text'):
            print(f"[DEBUG] Zoom API error response: {e.response.text}")
        
        if "start_time" in error_message.lower() or "recurrence" in error_message.lower() or "body" in error_message.lower():
            print(f"[DEBUG] Error creating Zoom meeting: {error_message}")
            # No fallback - we only want meetings by day of week (type=2)
            print(f"[DEBUG] Only supporting meetings by day of week, not attempting calendar day fallback")
                    
        # Check if there's already a meeting ID we can use from a partially successful response
        if 'resp' in locals() and resp is not None and resp.status_code == 201:
            try:
                response_data = resp.json()
                if 'id' in response_data and 'join_url' in response_data:
                    print(f"[DEBUG] Despite errors, we have a valid meeting ID: {response_data['id']}")
                    return response_data["join_url"], response_data["id"]
            except:
                pass
        
        # If we still don't have a successful response, re-raise the error
        raise

def check_and_fix_recurrence_pattern(meeting_id, expected_pattern, response_data=None):
    """
    Checks and fixes the recurrence pattern of a Zoom meeting.
    If the meeting has a weekly pattern but should be monthly, attempts to fix it.
    
    Args:
        meeting_id: The Zoom meeting ID
        expected_pattern: The expected recurrence pattern (e.g., "monthly")
        response_data: Optional response data from meeting creation
        
    Returns:
        Updated response data if a fix was applied, or None if no fix was needed or possible
    """
    try:
        # If we don't have response data, fetch the meeting
        if not response_data:
            meeting_details = get_meeting(meeting_id)
            response_data = meeting_details
        
        # Check if this is a recurring meeting
        if response_data.get("type") != 8:  # 8 is recurring meeting with fixed time
            print(f"[DEBUG] Meeting {meeting_id} is not a recurring meeting (type={response_data.get('type')}). No fix needed.")
            return None
            
        # Check the recurrence pattern
        recurrence = response_data.get("recurrence", {})
        current_type = recurrence.get("type")
        
        # If weekly but should be monthly
        if current_type == 2 and "weekly_days" in recurrence and expected_pattern == "monthly":
            print(f"[DEBUG] Meeting {meeting_id} has incorrect pattern: weekly when it should be monthly")
            
            # Get access token for API calls
            access_token = get_access_token()
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            
            # Get the first occurrence date
            if "occurrences" in response_data and response_data["occurrences"]:
                first_occurrence = response_data["occurrences"][0]
                first_occurrence_time = first_occurrence.get("start_time")
                if first_occurrence_time:
                    # Parse the date to determine the weekday and week of month
                    occurrence_dt = datetime.fromisoformat(first_occurrence_time.replace("Z", "+00:00"))
                    day_of_week = occurrence_dt.weekday() + 1  # 1=Monday, 7=Sunday
                    day_of_month = occurrence_dt.day
                    week_number = (day_of_month - 1) // 7 + 1
                    
                    # Weekday in Zoom's format (1=Sunday, 7=Saturday)
                    monthly_week_day_format = day_of_week % 7 + 1
                    
                    # Create the correct monthly recurrence
                    corrected_recurrence = {
                        "type": 3,  # Monthly 
                        "repeat_interval": 1,
                        "monthly_week": week_number,  # 1-4 or -1 for last week
                        "monthly_week_day": monthly_week_day_format,  # 1-7 where 1=Sunday
                        "end_times": recurrence.get("end_times", 12)
                    }
                    
                    # Update the meeting
                    update_url = f"{api_base_url}/meetings/{meeting_id}/recurrence"
                    print(f"[DEBUG] Attempting to fix meeting {meeting_id} pattern to monthly with payload:")
                    print(json.dumps(corrected_recurrence, indent=2))
                    
                    try:
                        resp = requests.patch(update_url, headers=headers, json=corrected_recurrence)
                        
                        if resp.status_code == 204:
                            print(f"[DEBUG] Successfully updated meeting {meeting_id} to monthly pattern")
                            # Fetch the updated meeting details
                            updated_meeting = get_meeting(meeting_id)
                            return updated_meeting
                        else:
                            print(f"[DEBUG] Failed to update meeting pattern: {resp.status_code} {resp.text}")
                    except Exception as e:
                        print(f"[DEBUG] Error updating meeting pattern: {str(e)}")
            
            return None
    except Exception as e:
        print(f"[DEBUG] Error in check_and_fix_recurrence_pattern: {str(e)}")
        return None
    
    return None

