import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import re  # Import regex module for text processing


# List of Goodreads URLs to check and scrape
urls = [
    "https://www.goodreads.com/list/show/146629", # Best Fantasy of the 2020s
    # "https://www.goodreads.com/list/show/38633",  # Best Fantasy of the 2010s
    # "https://www.goodreads.com/list/show/38609", # Best Fantasy of the 2000s
    # "https://www.goodreads.com/list/show/1118", # Best Fantasy of the 90s
    # "https://www.goodreads.com/list/show/1117", # Best Fantasy of the 80s
    # "https://www.goodreads.com/list/show/1116", # Best Fantasy of the 70s
    # "https://www.goodreads.com/list/show/75425", # Best Fantasy of the 60s
    # "https://www.goodreads.com/list/show/88", # Best Fantasy Books of the 21st Century
    # "https://www.goodreads.com/list/show/51", # The Best Urban Fantasy
    # "https://www.goodreads.com/list/show/50" # The Best Epic Fantasy (fiction)
]

# Set up headers to mimic a browser request to avoid blocking
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}


def check_valid_url(url):
    """
    Checks if a given URL is valid by making an HTTP GET request.

    Args:
    - url (str): The URL to check.

    Returns:
    - bool: True if the URL returns a status code of 200 (OK), False otherwise.
    """
    try:
        response = requests.get(url, headers=headers)
        return response.status_code == 200  # Return True if status code is 200
    except requests.exceptions.RequestException as e:
        print(f"Failed to reach the URL: {url}")
        print(f"Error: {e}")
        return False  # Return False if there was an exception


# Function to fetch books from a page, apply filtering, and return a list of filtered books with their authors
def fetch_books_from_page(page_content):
    """
    Extracts and filters book data (title, rating, number of ratings, author) from a Goodreads list page.

    Args:
    - page_content (str): The HTML content of the page.

    Returns:
    - tuple: Four lists containing filtered book titles, ratings, number of ratings, and authors.
    """
    # Parse the HTML content with BeautifulSoup
    soup = BeautifulSoup(page_content, 'html.parser')

    # Lists to hold the filtered data from this page
    titles = []
    ratings = []
    num_ratings_list = []
    authors = []

    # Find all book containers on the page
    books = soup.find_all('tr', itemtype='http://schema.org/Book')

    # Iterate over each book found on the page
    for book in books:
        # Extract the book title from the <a> tag with the class 'bookTitle'
        title_tag = book.find('a', class_='bookTitle')
        title = title_tag.get_text(strip=True)  # Remove any leading/trailing whitespace from the title

        # Extract the rating and number of ratings from the <span> tag with the class 'minirating'
        rating_tag = book.find('span', class_='minirating')
        rating_text = rating_tag.get_text(
            strip=True)  # Get the full rating text (e.g., "4.00 avg rating — 10,000 ratings")

        # Use regex to extract the numeric rating from the text (e.g., "4.00")
        rating_match = re.search(r"(\d+\.\d+)", rating_text)  # Look for patterns like "4.00", "3.75", etc.
        if rating_match:
            rating = float(rating_match.group(1))  # Convert the matched rating string to a float
        else:
            continue  # If no valid rating is found, skip this book and move to the next one

        # Use regex to extract the number of ratings from the text (e.g., "10,000 ratings")
        num_ratings_match = re.search(r"(\d+(?:,\d+)*) ratings", rating_text)  # Look for patterns like "1,234 ratings"
        if num_ratings_match:
            num_ratings = int(num_ratings_match.group(1).replace(",", ""))  # Remove commas and convert to integer
        else:
            continue  # If no valid number of ratings is found, skip this book and move to the next one

        # Extract the author name from the <a> tag with the class 'authorName'
        author_tag = book.find('a', class_='authorName')
        if author_tag:
            author = author_tag.get_text(strip=True)  # Remove any leading/trailing whitespace from the author name
        else:
            author = "Unknown"  # Default to "Unknown" if no author name is found

        # Apply the filtering conditions: rating > 3.00 and more than 100 ratings
        if rating > 3.00 and num_ratings > 100:
            titles.append(title)  # Add the title to the list
            ratings.append(rating)  # Add the rating to the list
            num_ratings_list.append(num_ratings)  # Add the number of ratings to the list
            authors.append(author)  # Add the author to the list

    return titles, ratings, num_ratings_list, authors


# Lists to hold the filtered data from all pages
all_titles = []
all_ratings = []
all_number_of_ratings = []
all_authors = []

# Loop through each URL, check if it's valid, and scrape the data if it is
for base_url in urls:
    if not check_valid_url(base_url):
        print(f"Skipping invalid URL: {base_url}")
        continue

    page_num = 1  # Start with the first page of each list
    while True:
        print(f"Fetching page {page_num} from {base_url}...")
        # Make the request to fetch the current page of books
        response = requests.get(base_url + f"?page={page_num}", headers=headers)

        # Check if the request was successful (status code 200)
        if response.status_code != 200:
            print(f"Error fetching the page: {response.status_code}")
            break  # Exit the loop if the request fails

        # Fetch and filter books from the current page
        titles, ratings, num_ratings_list, authors = fetch_books_from_page(response.text)

        # If no books were found on the current page, exit the loop
        if not titles:
            print(f"No more books found on page {page_num}. Moving to next URL.")
            break

        # Append the filtered data from this page to the overall lists
        all_titles.extend(titles)
        all_ratings.extend(ratings)
        all_number_of_ratings.extend(num_ratings_list)
        all_authors.extend(authors)

        # Move to the next page
        page_num += 1

        # Sleep for 2 seconds between requests to avoid overwhelming the server and reduce the risk of being blocked
        time.sleep(2)

# After scraping all pages, create a DataFrame to store the filtered data
data = {
    'Title': all_titles,
    'Author': all_authors,
    'Rating': all_ratings,
    'Number of Ratings': all_number_of_ratings
}
df = pd.DataFrame(data)  # Convert the data into a Pandas DataFrame

# Remove duplicates by title to ensure there are no duplicate entries
df.drop_duplicates(subset='Title', inplace=True)

# Save the filtered and deduplicated data to a CSV file for further analysis or review
df.to_csv('filtered_fantasy_books.csv', index=False)
print("Data saved to filtered_fantasy_books.csv")

# Display the first few rows of the DataFrame to verify the data
print(df.head())

# Print the total number of books that met the filtering criteria
print(f"Total number of unique books found: {len(df)}")