import requests
from bs4 import BeautifulSoup
import pandas as pd
import re

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


def fetch_page_content(url):
    """
    Fetches the content of a webpage.

    Args:
    - url (str): The URL of the page to fetch.

    Returns:
    - str: The HTML content of the page if successful, None otherwise.
    """
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.text  # Return the HTML content if the request is successful
        else:
            print(f"Error fetching page content: {response.status_code}")
            return None  # Return None if the request fails
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch page content: {e}")
        return None  # Handle any network-related exceptions and return None


def fetch_books_from_page(page_content):
    """
    Extracts and filters book data (title, rating, number of ratings, author, and detail page link)
    from a Goodreads list page.

    Args:
    - page_content (str): The HTML content of the page.

    Returns:
    - list of dict: A list of dictionaries containing the book data (title, rating, number of ratings, author, link).
    """
    soup = BeautifulSoup(page_content, 'html.parser')  # Parse the page content using BeautifulSoup
    books = soup.find_all('tr', itemtype='http://schema.org/Book')  # Find all book containers on the page

    filtered_books = []  # List to hold filtered book data

    # Iterate through each book found on the page, stop after processing 5 books
    for i, book in enumerate(books):
        if i >= 5:  # Only process the first 5 books
            break

        # Extract the book title and detail page link
        title_tag = book.find('a', class_='bookTitle')
        title = title_tag.get_text(strip=True)  # Clean up the title text
        book_link = "https://www.goodreads.com" + title_tag['href']  # Build the full URL for the book detail page

        # Extract the author name from the <a> tag with the class 'authorName'
        author_tag = book.find('a', class_='authorName')
        author = author_tag.get_text(strip=True) if author_tag else "Unknown"  # Extract and clean the author name

        # Extract the rating and number of ratings from the 'minirating' span tag
        rating_tag = book.find('span', class_='minirating')
        rating_text = rating_tag.get_text(strip=True)

        # Use regex to extract numeric rating (e.g., "4.00")
        rating_match = re.search(r"(\d+\.\d+)", rating_text)
        if rating_match:
            rating = float(rating_match.group(1))  # Convert the matched rating string to a float
        else:
            continue  # Skip the book if no valid rating is found

        # Use regex to extract the number of ratings (e.g., "10,000 ratings")
        num_ratings_match = re.search(r"(\d+(?:,\d+)*) ratings", rating_text)
        if num_ratings_match:
            num_ratings = int(num_ratings_match.group(1).replace(",", ""))  # Convert to integer and remove commas
        else:
            continue  # Skip the book if no valid number of ratings is found

        # Apply filtering conditions: rating > 3.00 and more than 100 ratings
        if rating > 3.00 and num_ratings > 100:
            # Append the book details to the filtered_books list
            filtered_books.append({
                'title': title,
                'author': author,
                'rating': rating,
                'num_ratings': num_ratings,
                'link': book_link
            })

    return filtered_books  # Return the list of filtered books


def fetch_book_details(book_url):
    """
    Fetches the rating distribution for a book from its detail page.

    Args:
    - book_url (str): The URL of the book's detail page.

    Returns:
    - dict: A dictionary of rating distribution (number of ratings for each star rating).
    - str: The author's name.
    """
    page_content = fetch_page_content(book_url)  # Fetch the HTML content of the book's detail page
    if page_content is None:
        return {}, "Unknown"  # Return empty data if page fetch fails

    soup = BeautifulSoup(page_content, 'html.parser')  # Parse the page content using BeautifulSoup

    # Improved Fetching of rating distribution
    rating_distribution = {'5': 0, '4': 0, '3': 0, '2': 0, '1': 0}

    # Look for rating distribution in the review stats section (this can change based on Goodreads page structure)
    rating_histogram = soup.find_all('div', class_='ratingGraph')  # This div contains the rating distribution bars

    if rating_histogram:
        # The bars for each star rating are usually in the ratingGraph div, and within each bar, there should be a value showing the number of ratings
        for i, bar in enumerate(rating_histogram):
            star_rating = str(5 - i)  # Convert index to star rating (5 stars, 4 stars, ..., 1 star)
            rating_count = bar.find('span', class_='value').get_text(strip=True).replace(',',
                                                                                         '')  # Find the count within the bar
            if rating_count.isdigit():
                rating_distribution[star_rating] = int(rating_count)  # Store the count in the dictionary

    # Fetching the author name
    author_tag = soup.find('a', class_='authorName')  # Find the author name tag
    author_name = author_tag.get_text(strip=True) if author_tag else "Unknown"  # Extract and clean the author name

    return rating_distribution, author_name  # Return the rating distribution and author name


def process_books_from_url(base_url):
    """
    Processes the first 5 books from page 1 of a given Goodreads list URL, fetching detailed data for each book.

    Args:
    - base_url (str): The base URL of the Goodreads list page.

    Returns:
    - list of dict: A list of dictionaries containing data for each book (title, rating, number of ratings, author, rating distribution).
    """
    page_num = 1  # We only want to process the first page
    all_books = []  # List to hold all books

    print(f"Fetching page {page_num} from {base_url}...")
    page_content = fetch_page_content(base_url + f"?page={page_num}")  # Fetch the content of page 1

    if not page_content:
        return []  # Stop fetching if no content is returned

    books = fetch_books_from_page(page_content)  # Extract book data from the first 5 books on the page
    if not books:
        print(f"No more books found on page {page_num}. Moving to next URL.")
        return []  # Stop fetching if no more books are found

    # Loop through each of the first 5 books and fetch additional details
    for book in books:
        rating_distribution = fetch_book_details(book['link'])  # Fetch additional details for the book
        # Add the additional details to the book data
        book['rating_distribution'] = rating_distribution
        all_books.append(book)  # Append the book data to the all_books list

    return all_books  # Return the list of all books


def save_books_to_csv(books, filename):
    """
    Saves the book data to a CSV file.

    Args:
    - books (list of dict): The list of dictionaries containing book data.
    - filename (str): The name of the CSV file to save the data to.
    """
    df = pd.DataFrame(books)  # Convert the list of books to a pandas DataFrame
    df.drop_duplicates(subset='title', inplace=True)  # Remove duplicates by title
    df.to_csv(filename, index=False)  # Save the DataFrame to CSV
    print(f"Data saved to {filename}")


def main():
    """
    Main function to process all URLs, fetch book data, and save it to a CSV file.
    """
    all_books = []  # Initialize an empty list to hold all books

    # Loop through each URL in the list
    for base_url in urls:
        books = process_books_from_url(base_url)  # Process the first 5 books from page 1 of the current URL
        all_books.extend(books)  # Append the fetched books to the master list

    # Save the data to a CSV file
    save_books_to_csv(all_books, 'filtered_first_5_books_with_ratings_and_authors.csv')

    # Print the total number of books found
    print(f"Total number of unique books found: {len(all_books)}")


if __name__ == "__main__":
    main()  # Run the main function if this script is executed directly