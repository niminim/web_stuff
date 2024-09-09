import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import re  # Import regex module for text processing


# List of Goodreads URLs to check and scrape
urls = [
    "https://www.goodreads.com/list/show/146629",
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
    print("fetch book content")
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            print("fetch book content1!")
            return response.text  # Return the HTML content if the request is successful
        else:
            print(f"Error fetching page content: {response.status_code}")
            print("fetch book content2!")
            return None  # Return None if the request fails
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch page content: {e}")
        print("fetch book content error!")
        return None  # Handle any network-related exceptions and return None


def fetch_books_from_page(page_content):
    """
    Extracts and filters book data (title, rating, number of ratings, and detail page link) from a Goodreads list page.

    Args:
    - page_content (str): The HTML content of the page.

    Returns:
    - list of dict: A list of dictionaries containing the book data (title, rating, number of ratings, link).
    """
    print("fetch_books_from_page")
    soup = BeautifulSoup(page_content, 'html.parser')  # Parse the page content using BeautifulSoup
    books = soup.find_all('tr', itemtype='http://schema.org/Book')  # Find all book containers on the page

    filtered_books = []  # List to hold filtered book data

    # Iterate through each book found on the page
    for book in books:
        # Extract the book title and detail page link
        print('find book')
        title_tag = book.find('a', class_='bookTitle')
        print('found book')
        title = title_tag.get_text(strip=True)  # Clean up the title text
        book_link = "https://www.goodreads.com" + title_tag['href']  # Build the full URL for the book detail page

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
                'rating': rating,
                'num_ratings': num_ratings,
                'link': book_link
            })

    print("finished fetch_books_from_page!")

    return filtered_books  # Return the list of filtered books


def fetch_book_details(book_url):
    """
    Fetches the rating distribution and author name for a book from its detail page.

    Args:
    - book_url (str): The URL of the book's detail page.

    Returns:
    - dict: A dictionary of rating distribution (number of ratings for each star rating).
    - str: The author's name.
    """
    print('fetching book details')
    page_content = fetch_page_content(book_url)  # Fetch the HTML content of the book's detail page
    if page_content is None:
        return {}, "Unknown"  # Return empty data if page fetch fails

    print('1')
    soup = BeautifulSoup(page_content, 'html.parser')  # Parse the page content using BeautifulSoup
    print('2')
    # Fetching rating distribution
    rating_distribution = {'5': 0, '4': 0, '3': 0, '2': 0, '1': 0}
    rating_distribution_section = soup.find_all('span', class_='greyText staticStars')
    print('3')
    for i, rating_element in enumerate(rating_distribution_section):
        print('i: ', i)
        star_rating = str(5 - i)  # Convert index to star rating (5, 4, 3, 2, 1)
        num_ratings_text = rating_element.get_text(strip=True).replace(',', '')  # Clean up the number formatting
        rating_distribution[star_rating] = int(
            num_ratings_text)  # Convert to integer and store in the rating distribution dictionary

    # Fetching the author name
    author_tag = soup.find('a', class_='authorName')  # Find the author name tag
    if author_tag:
        author_name = author_tag.get_text(strip=True)  # Extract and clean the author name
    else:
        author_name = "Unknown"  # Default to "Unknown" if the author name is not found

    print('finished fetching book details!')

    return rating_distribution, author_name  # Return the rating distribution and author name


def process_books_from_url(base_url):
    """
    Processes all books from a given Goodreads list URL, fetching detailed data for each book.

    Args:
    - base_url (str): The base URL of the Goodreads list page.

    Returns:
    - list of dict: A list of dictionaries containing data for each book (title, rating, number of ratings, author, rating distribution).
    """
    page_num = 1  # Initialize page number
    all_books = []  # List to hold all books

    # Loop through each page of the list until no more books are found
    while page_num==1:
        print(f"Fetching page {page_num} from {base_url}...")
        page_content = fetch_page_content(base_url + f"?page={page_num}")  # Fetch the content of the current page

        if not page_content:
            break  # Stop fetching if no content is returned

        books = fetch_books_from_page(page_content)  # Extract book data from the page
        if not books:
            print(f"No more books found on page {page_num}. Moving to next URL.")
            break  # Stop fetching if no more books are found

        n_books = 0
        # Loop through each book and fetch additional details
        for book in books:
            print('n_books: ', n_books)
            n_books +=1
            rating_distribution, author_name = fetch_book_details(book['link'])  # Fetch additional details for the book
            # Add the additional details to the book data
            book['rating_distribution'] = rating_distribution
            book['author'] = author_name
            all_books.append(book)  # Append the book data to the all_books list

        page_num += 1  # Move to the next page
        # time.sleep(1)  # Reduce sleep time to 1 second between requests for faster execution

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
        books = process_books_from_url(base_url)  # Process the books from the current URL
        all_books.extend(books)  # Append the fetched books to the master list

    # Save the data to a CSV file
    save_books_to_csv(all_books, 'filtered_fantasy_books_with_ratings_and_authors.csv')

    # Print the total number of books found
    print(f"Total number of unique books found: {len(all_books)}")


if __name__ == "__main__":
    main()  # Run the main function if this script is executed directly