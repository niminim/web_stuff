import pandas as pd
import numpy as np

from investing.gf_analyze_ticker import get_financial_data_for_ticker


### The code gets a ticker_list and a dataframe (with Symbol and Company)
### and returns the best companies (according to Gurufocus data) according to predefined rules

# code for get_companies_from_wiki was here first

## Make a dataframe of all best companies

# Define a function to extract and handle the score
def extract_scores(main_scores, key):
    try:
        return int(main_scores[key].split('/')[0])
    except (ValueError, KeyError):
        return np.nan


def get_best_companies(ticker_list, df):
    # go over all companies in Gurufocus, extracts scores

    # Create a blank DataFrame with specified columns
    df_best_companies = pd.DataFrame(
        columns=['ticker', 'company_name', 'financial_str', 'profit', 'growth', 'gf_value', 'momentum']
    )

    # find best companies
    for i, ticker in enumerate(ticker_list):
        print(f'i: {i+1} - ticker: {ticker}')
        main_scores, all_data = get_financial_data_for_ticker(ticker, print_all_data=False)

        # for key in score_keys:
        #     scores[key] = extract_score(main_scores, key)
        scores = {key: extract_scores(main_scores, key) for key in main_scores.keys()}

        if (scores['financial_str']>=8) & (scores['profit']>=8):

            new_row = pd.DataFrame({
                'ticker': [ticker],
                'company_name': [df.loc[ticker]['Company']], # for option 1 - ticker is index
                # 'company_name': [df[df['Symbol'] == ticker]['Security'].values[0]], # option2 after index reset
                'financial_str': [scores['financial_str']],
                'profit': [scores['profit']],
                'growth': [scores['growth']],
                'gf_value': [scores['gf_value']],
                'momentum': [scores['momentum']],
                'GF_score': [scores['GF_score']],

            })
            df_best_companies = pd.concat([df_best_companies, new_row], ignore_index=True) # add the current company

    df_best_companies = df_best_companies.sort_values(by='GF_score', ascending=False) # Sort companies by GF Score
    # df_best_companies.to_csv("/home/nim/best_companies.csv", index=False)

    return df_best_companies

# df_best_companies = get_best_companies(ticker_list, df)

# to rename a column
# df_best_companies.rename(columns={'financial_str_score': 'financial_str'}, inplace=True)




