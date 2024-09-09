import requests
from bs4 import BeautifulSoup
import pandas as pd


###  The code gets a url that includes a table of companies a the table as a dataframe
### I can also gets the full financial data from Stockanalysis


# https://stockanalysis.com/list/
sp500_url = 'https://stockanalysis.com/list/sp-500-stocks/'
nasdaq100_rul = 'https://stockanalysis.com/list/nasdaq-100-stocks/'
nasdaq_url = 'https://stockanalysis.com/list/nasdaq-stocks/'
nyse_url = 'https://stockanalysis.com/list/nyse-stocks/'
israeli_us_url= 'https://stockanalysis.com/list/israeli-stocks-us/'
ipos_url = 'https://stockanalysis.com/ipos/'

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


# Function to parse the table data from HTML content
def parse_table_data(html_content):
    # parse the data table
    soup = BeautifulSoup(html_content, 'html.parser')

    # Find the table in the page content
    table = soup.find('table')

    if not table:
        print("Table not found on the page.")
        return None, None

    # Extract table headers
    headers = []
    for header in table.find_all('th'):
        headers.append(header.get_text().strip())

    # Extract table rows
    rows = []
    for row in table.find_all('tr')[1:]:  # Skip the header row
        cells = row.find_all('td')
        row_data = [cell.get_text().strip() for cell in cells]
        rows.append(row_data)

    return headers, rows


# Main function to get the S&P 500 table data
def get_data_table(url):
    # Fetch the page content
    html_content = fetch_html(url)

    if html_content:
        # Parse the table data
        headers, table_data = parse_table_data(html_content)

        # Convert the table data to a pandas DataFrame
        df = pd.DataFrame(table_data, columns=headers)
        df.rename(columns={'Company Name': 'Company'}, inplace=True)

        return df
    else:
        return None


# Define the function to fetch and process financial data for a specific company
def get_company_financials_as_df(ticker):
    # the functions gets a company ticker and returs a dictionary of financial tables (each as a dataframe)
    financials_urls = {
        'income': f"https://stockanalysis.com/stocks/{ticker}/financials/",
        'balance_sheet': f"https://stockanalysis.com/stocks/{ticker}/financials/balance-sheet/",
        'cash_flow': f"https://stockanalysis.com/stocks/{ticker}/financials/cash-flow-statement/",
        'ratios': f"https://stockanalysis.com/stocks/{ticker}/financials/ratios/"
    }

    return {key: get_data_table(url) for key, url in financials_urls.items()}


def get_full_data_from_table_dfs(comp_df):
    # retunrs data from table dataframes is a fully dictionary (then you can accesss anything using this dictionary)

    full_data_dict = {}
    for key in comp_df.keys():
        table_categories_list = list(comp_df[key]['Year Ending'].values) # categories appearing in each table

        full_data_dict[key] = {}
        if key == 'ratios':
            for category in table_categories_list:
                full_data_dict[key][category] = comp_df[key][comp_df[key]['Year Ending'] == category]['Current'].values[0]
        else:
            for category in table_categories_list:
                full_data_dict[key][category] = comp_df[key][comp_df[key]['Year Ending'] == category]['TTM'].values[0]

    return full_data_dict

# sp500_df = get_data_table(sp500_url)
ipos_df = get_data_table(ipos_url)
ticker_list = list(ipos_df['Symbol'].values)


dov_financials_df = get_company_financials_as_df(ticker='dov')
dov_financials_df['ratios'][dov_financials_df['ratios']['Year Ending'] == 'Debt / Equity Ratio']['Current'].values[0]

full_data_dict = get_full_data_from_table_dfs(dov_financials_df)


