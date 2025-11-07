import csv
import json
import requests
from bs4 import BeautifulSoup


with open("vb_rosters_2023_24.csv", mode="w") as csv_file:
    writer = csv.writer(csv_file)

    # Write the CSV headers
    writer.writerow(["School", "Name", "Position", "Class", "Height","Hometown", "High School"])

    # Loop through the URLs
    teams_json = json.loads(open('teams.json').read())
    teams_with_urls = [x for x in teams_json if "url" in x]
    for team in teams_with_urls:
        url = team['url'] + "/roster/"
        school_name = team['team']
        print(url)
        try:
            # Send a GET request to the URL
            response = requests.get(url)
            response.raise_for_status()  # Check for HTTP errors

            # Parse the HTML content
            soup = BeautifulSoup(response.content, "html.parser")

            # Extract the school name from the <title> tag within the <head> section
            # First pass: Maryland template
            player_details = soup.find_all("div", class_="s-person-details")

            if player_details:
                for player in player_details:
                    # Extract player information
                    name = player.find("div", class_="s-person-details__personal-single-line").find("span").text.strip()
                    position = player.find("span", class_="s-person-details__bio-stats-item").text.strip()
                    class_year = player.find_all("span", class_="s-person-details__bio-stats-item")[1].text.strip()
                    height = player.find_all("span", class_="s-person-details__bio-stats-item")[2].text.strip()

                    # Find player location details in the HTML:
                    location_info = player.find_next("div", class_="s-person-card__content__person-contact-info")
                    hometown = location_info.find_all("span", class_="s-person-card__content__person__location-item")[0].text.strip()
                    high_school = location_info.find_all("span", class_="s-person-card__content__person__location-item")[1].text.strip()

                    # Write the players' information to the CSV file:
                    writer.writerow([school_name, name, position, class_year, height, hometown, high_school])

            # Second pass: Alabama template:
            elif soup.find_all(class_='sidearm-roster-player-container'):
                player_containers = soup.find_all(class_="sidearm-roster-player-container")

                for player_container in player_containers:
                    position = player_container.find(class_="sidearm-roster-player-position").text.strip()
                    name = player_container.find(class_="sidearm-roster-player-name").text.strip()

                    # Remove extra information from position (height and weight)
                    position = position.split('\n')[0].strip()

                    # Remove extra information from name (jersey number)
                    name = name.split('\n')[-1].strip()

                    class_year = player_container.find(class_="sidearm-roster-player-academic-year").text.strip()
                    height = None
                    hometown = player_container.find(class_="sidearm-roster-player-hometown").text.strip()
                    high_school = player_container.find(class_="sidearm-roster-player-highschool").text.strip()

                    # Write the players' information to the CSV file:
                    writer.writerow([school_name, name, position, class_year, height, hometown, high_school])


        except requests.exceptions.HTTPError as errh:
            print(f"HTTP Error: {errh}")
            print(f"Failed to retrieve the web page ({url}). Status code:", response.status_code)
        except Exception as err:
            print(f"An error occurred while processing {url}: {err}")
