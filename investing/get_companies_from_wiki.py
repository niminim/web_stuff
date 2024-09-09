import pandas as pd
import numpy as np
# pip install lxml

# https://stackoverflow.com/questions/44232578/get-the-sp-500-tickers-list
def list_wikipedia_sp500() -> pd.DataFrame:
    # Ref: https://stackoverflow.com/a/75845569/
    url = 'https://en.m.wikipedia.org/wiki/List_of_S%26P_500_companies'
    return pd.read_html(url, attrs={'id': 'constituents'}, index_col='Symbol')[0]

df = list_wikipedia_sp500()
df.rename(columns={'Security': 'Company'}, inplace=True)
df.head()


for i, row in enumerate(df.iterrows()):
    print(f"{i+1}. Ticker: {row[0]}, Company: {row[1]['Company']}")

mmm_row = df.loc['MMM']
ticker_list = list(df.index)

df.to_csv("/home/nim/output_1.csv", index=True) # for option 1
#######


# ###### Symbol is now a column rather than the index (option 2)
# df = df.reset_index()  # This will convert the current index into a column
# df.rename(columns={'Security': 'Company'}, inplace=True)
#
# # mmm_row = df[df['Symbol'] == 'MMM']
#
# for i, row in enumerate(df.iterrows()):
#     print(f"{i+1}. Ticker: {row[1]['Symbol']}, Company: {row[1]['Company']}")
#
# for i, row in df.iterrows():
#     print(f"{i+1}. Ticker: {row['Symbol']}, Company: {row['Company']}")
#
# ticker_list = list(df['Symbol'])
# df.to_csv("/home/nim/output_1.csv", index=False) # for option 2
# ######
