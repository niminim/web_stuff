import yfinance as yf
# pip install yfinance
# https://gist.github.com/quantra-go-algo/ac5180bf164a7894f70969fa563627b2

def get_company_name_from_ticker(ticker):
    stock = yf.Ticker(ticker)
    company_info = stock.info

    # Get the company name from the 'longName' field
    company_name = company_info.get('longName', 'Company name not found')

    return company_name


def print_all_data_from_ticker(ticker):
    stock = yf.Ticker(ticker)
    company_info = stock.info

    # Print all data retrieved from the ticker
    for key, value in company_info.items():
        print(f"{key}: {value}")


# Example usage
ticker = 'AAPL'
company_name = get_company_name_from_ticker(ticker)
print(f"The company name for {ticker} is: {company_name}")


# Example usage
ticker = 'AAPL'
print_all_data_from_ticker(ticker)