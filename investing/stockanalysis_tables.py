import requests
from bs4 import BeautifulSoup
import pandas as pd


###  The code gets a url that includes a table of companies a the table as a dataframe
### I can also get the full financial data from Stockanalysis


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


def clean_headers(headers):
    # Find the index of 'Period Ending'

    cleaned = False
    if 'Period Ending' in headers:
        index = headers.index('Period Ending')
        # Slice the list to include only the headers before 'Period Ending'
        headers = headers[:index]
        cleaned = True
    return headers, cleaned

# Main function to get the S&P 500 table data
def get_data_table(url):
    # Fetch the page content
    html_content = fetch_html(url)

    if html_content:
        # Parse the table data
        headers, table_data = parse_table_data(html_content)
        headers, cleaned = clean_headers(headers) # throw second row of categories

        # Convert the table data to a pandas DataFrame
        df = pd.DataFrame(table_data, columns=headers)
        df.rename(columns={'Company Name': 'Company'}, inplace=True)

        if cleaned:
            # Drop the row with index 0
            ratios_cleaned = df.drop(index=0)

            # Reset index if needed
            ratios_cleaned.reset_index(drop=True, inplace=True)

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

    df = {key: get_data_table(url) for key, url in financials_urls.items()}
    return df


def _find_label_col(df):
    # common possibilities seen on StockAnalysis tables
    candidates = ['Fiscal Year', 'Year Ending', 'Period Ending', 'Metric', 'Breakdown', 'Category', 'Item']
    for c in candidates:
        if c in df.columns:
            return c
    # as a fallback, assume the leftmost column is the label column
    return df.columns[0]

def get_full_data_from_table_dfs(comp_df):
    """
    Returns a nested dict:
      full_data_dict[table_key][row_label] = value_from_desired_column
    For ratios -> 'Current'
    For others -> 'TTM'
    """
    full_data_dict = {}
    for key, df in comp_df.items():
        if df is None or df.empty:
            full_data_dict[key] = {}
            continue

        label_col = _find_label_col(df)

        # choose the value column depending on table type
        value_col = 'Current' if key == 'ratios' else 'TTM'
        if value_col not in df.columns:
            # if TTM/Current missing, pick the last numeric-ish column as a fallback
            numeric_like = [c for c in df.columns if c != label_col]
            value_col = numeric_like[-1] if numeric_like else df.columns[-1]

        full_data_dict[key] = {}
        for category in df[label_col].dropna().tolist():
            row = df[df[label_col] == category]
            if not row.empty and value_col in row.columns:
                full_data_dict[key][category] = row.iloc[0][value_col]
            else:
                full_data_dict[key][category] = None

    return full_data_dict

sp500_df = get_data_table(sp500_url)
# ipos_df = get_data_table(ipos_url)
ticker_list = list(sp500_df['Symbol'].values)


dov_financials_df = get_company_financials_as_df(ticker='dov')
dov_financials_df['ratios'][dov_financials_df['ratios']['Fiscal Year'] == 'Debt / Equity Ratio']['Current'].values[0]

full_data_dict = get_full_data_from_table_dfs(dov_financials_df)


# Changes:
# added another row of categories (right under the first)
# changes 'Year Ending' to 'Fiscal Year'