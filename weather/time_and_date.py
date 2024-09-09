import requests
from bs4 import BeautifulSoup


# Function to fetch the HTML content of the page
def fetch_weather_page(url):
    # Send a GET request to the specified URL
    response = requests.get(url)

    # If the request was successful (status code 200)
    if response.status_code == 200:
        # Parse the HTML content using BeautifulSoup and return it
        return BeautifulSoup(response.text, 'html.parser')
    else:
        # Print an error message if the request failed
        print(f"Failed to retrieve data. Status code: {response.status_code}")
        return None


# Function to extract the temperature
def extract_temperature(soup):
    # Find the 'div' element with class 'h2' and return the text (current temperature)
    return soup.find('div', class_='h2').text.strip()


# Function to extract the weather description
def extract_weather_description(soup):
    # Find the first 'p' element and return the text (e.g., "Passing clouds")
    return soup.find('p').text.strip()


# Function to extract humidity
def extract_humidity(soup):
    # Search for the text "Humidity" in the HTML and find the corresponding element
    humidity_text = soup.find(string=lambda x: "Humidity" in x)

    # If "Humidity" text is found, get the next element (where the value is stored) and return its text
    if humidity_text:
        return humidity_text.find_next().text.strip()

    # If "Humidity" is not found, return "N/A"
    return "N/A"


# Function to extract "Feels Like" temperature
def extract_feels_like(soup):
    # Search for the text "Feels Like" in the HTML
    feels_like_text = soup.find(string=lambda x: "Feels Like" in x)

    # If "Feels Like" text is found, split the string to get the value after the colon and return it
    if feels_like_text:
        return feels_like_text.split(":")[1].strip()

    # If "Feels Like" is not found, return "N/A"
    return "N/A"


# Function to extract the forecast
def extract_forecast(soup):
    # Find the 'span' element with the title "High and low forecasted temperature today"
    forecast_span = soup.find('span', title="High and low forecasted temperature today")

    # If the forecast element is found, remove the redundant "Forecast:" label and return the value
    if forecast_span:
        return forecast_span.text.replace("Forecast:", "").strip()

    # If the forecast element is not found, return "N/A"
    return "N/A"


# Function to extract wind speed and direction
def extract_wind_info(soup):
    # Search for the text "Wind:" in the HTML and get its parent element
    wind_section = soup.find(string=lambda x: "Wind:" in x).parent

    # If the wind section is found, split the text to separate wind speed from direction
    if wind_section:
        wind_info = wind_section.text.split("Wind:")[1].strip()

        # Extract the wind speed (e.g., "24 km/h")
        wind_speed = wind_info.split(" ")[0] + " km/h"

        # Extract the wind direction (e.g., "from Northwest")
        wind_direction = " ".join(wind_info.split(" ")[3:]).strip()

        # Return both wind speed and wind direction
        return wind_speed, wind_direction

    # If the wind section is not found, return "N/A" for both wind speed and direction
    return "N/A", "N/A"


# Main function to extract all weather data and print it
def extract_weather_data(url):
    # Fetch the parsed HTML content of the weather page
    soup = fetch_weather_page(url)

    # If the page was successfully fetched and parsed
    if soup:
        # Extract temperature, weather description, humidity, feels like temperature, forecast, and wind info
        temperature = extract_temperature(soup)
        weather_desc = extract_weather_description(soup)
        humidity = extract_humidity(soup)
        feels_like = extract_feels_like(soup)
        forecast = extract_forecast(soup)
        wind_speed, wind_direction = extract_wind_info(soup)

        # Print all extracted weather data
        print(f"Current weather in {url.split('/')[-1]}")
        print(f"Temperature: {temperature}")
        print(f"Weather: {weather_desc}")
        print(f"Feels Like: {feels_like}")
        print(f"Forecast: {forecast}")
        print(f"Wind Speed: {wind_speed}")
        print(f"Wind Direction: {wind_direction}")
        print(f"Humidity: {humidity}")


# URL of the weather page  for Tel Aviv
url = 'https://www.timeanddate.com/weather/israel/tel-aviv'
url = 'https://www.timeanddate.com/weather/israel/petah-tikva'

# Call the main function to extract and print weather data
extract_weather_data(url)
