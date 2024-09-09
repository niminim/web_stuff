import requests
from bs4 import BeautifulSoup
import re

# Custom headers to mimic a browser
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

# Function to fetch the HTML content of the page
def fetch_html(url):
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.text
    else:
        print(f"Failed to retrieve data. Status code: {response.status_code}")
        return None

# General function to extract rank scores
def extract_rank_score(soup, rank_name, identifier):
    rank_section = soup.find('a', href=lambda href: href and identifier in href)

    if rank_section:
        score_div = rank_section.find_next('div', class_='indicator-progress-bar-header')

        if score_div:
            style_attr = score_div.div.get('style')
            if style_attr:
                score_percentage = style_attr.split('width:')[1].split('%')[0].strip()
                score = int(float(score_percentage) / 10)
                return f"{score}/10"

    return f"{rank_name} score not found."


def extract_gf_score(html_content):
    # Regex pattern to find gf_score: followed by a number
    pattern = r'gf_score:(\d+)'

    # Search for the pattern in the HTML content
    match = re.search(pattern, html_content)

    if match:
        gf = int(match.group(1))
        return f"{gf}/100" # Return the score (first captured group) as a string (out of 100)
    else:
        return "GF Score not found."

# # Function to parse and extract the GF Score
# def extract_gf_score(soup):
#     gf_score_label = soup.find('span', string="GF Score:")
#     if gf_score_label:
#         # The score is located two siblings after the "GF Score:" label
#         gf_score_value = gf_score_label.find_next('span', class_='t-primary')
#         if gf_score_value:
#             return gf_score_value.get_text(strip=True)
#     return "GF Score not found."


# Function to parse and extract other financial data from tables
def extract_financial_data(soup):
    financial_data = {}

    # Extracting data from rows with financial metrics
    rows = soup.find_all('tr', class_='stock-indicators-table-row')
    for row in rows:
        metric_name_tag = row.find('td', class_='t-caption p-v-sm semi-bold')
        metric_value_tag = row.find('span', class_='p-l-sm')

        if metric_name_tag and metric_value_tag:
            metric_name = metric_name_tag.get_text(strip=True)
            metric_value = metric_value_tag.get_text(strip=True)
            financial_data[metric_name] = metric_value

    return financial_data

# Main function to fetch and print financial data for any stock ticker
def get_financial_data_for_ticker(ticker, print_all_data):
    url = f'https://www.gurufocus.com/stock/{ticker}/summary'

    # Fetch the page content
    html_content = fetch_html(url)

    if html_content:
        soup = BeautifulSoup(html_content, 'html.parser')

        main_scores = {}
        # Extract various rank scores
        main_scores['financial_str'] = extract_rank_score(soup, "Financial Strength", 'rank-balancesheet')
        main_scores['profit'] = extract_rank_score(soup, "Profitability Rank", 'rank-profitability')
        main_scores['growth'] = extract_rank_score(soup, "Growth Rank", 'rank-growth')
        main_scores['gf_value'] = extract_rank_score(soup, "GF Value Rank", 'rank-gf-value')
        main_scores['momentum'] = extract_rank_score(soup, "Momentum Rank", 'rank-momentum')
        main_scores['GF_score'] = extract_gf_score(html_content)


        # Print the extracted scores and metrics
        print(f"Financial Strength Score for {ticker.upper()}: {main_scores['financial_str']}")
        print(f"Profitability Rank Score for {ticker.upper()}: {main_scores['profit']}")
        print(f"Growth Rank Score for {ticker.upper()}: {main_scores['growth']}")
        print(f"GF Value Rank Score for {ticker.upper()}: {main_scores['gf_value']}")
        print(f"Momentum Rank Score for {ticker.upper()}: {main_scores['momentum']}")
        print(f"GF Score {ticker.upper()}: {main_scores['GF_score']}")

        # Extract financial data from the table
        all_data = extract_financial_data(soup)

        # Print the extracted financial data
        if print_all_data:
            if all_data:
                print(f"\nOther Financial Data for {ticker.upper()}:")
                for key, value in all_data.items():
                    print(f"{key}: {value}")
            else:
                print("No additional financial data found.")

    return main_scores, all_data

# Example usage
ticker = 'NVDA'  # You can change this ticker symbol to fetch data for another company
ticker = 'MTSFY'  # You can change this ticker symbol to fetch data for another company

ticker = 'AAPL'
main_indicators, all_data = get_financial_data_for_ticker(ticker, print_all_data=True)

