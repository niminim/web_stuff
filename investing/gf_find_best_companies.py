# import pandas as pd
# import numpy as np
# import os
# import sys
#
# project_root = os.path.abspath("/home/nim/venv/web_stuff")
# sys.path.append(project_root)
#
# from investing.gf_analyze_ticker_new import get_financial_data_for_ticker
#
#
# ### The code gets a ticker_list and a dataframe (with Symbol and Company)
# ### and returns the best companies (according to Gurufocus data) according to predefined rules
#
# # code for get_companies_from_wiki was here first
#
# ## Make a dataframe of all best companies
#
# # Define a function to extract and handle the score
# def extract_scores(main_scores, key):
#     try:
#         return int(main_scores[key].split('/')[0])
#     except (ValueError, KeyError):
#         return np.nan
#
#
# def get_best_companies(ticker_list, df):
#     # go over all companies in Gurufocus, extracts scores
#
#     # Create a blank DataFrame with specified columns
#     df_best_companies = pd.DataFrame(
#         columns=['ticker', 'company_name', 'financial_str', 'profit', 'growth', 'gf_value', 'momentum']
#     )
#
#     # find best companies
#     for i, ticker in enumerate(ticker_list):
#         print(f'i: {i+1} - ticker: {ticker}')
#         main_scores, all_data = get_financial_data_for_ticker(ticker, print_all_data=False)
#
#         # for key in score_keys:
#         #     scores[key] = extract_score(main_scores, key)
#         scores = {key: extract_scores(main_scores, key) for key in main_scores.keys()}
#
#         if (scores['financial_str']>=8) & (scores['profit']>=8):
#
#             new_row = pd.DataFrame({
#                 'ticker': [ticker],
#                 'company_name': [df.loc[ticker]['Company']], # for option 1 - ticker is index
#                 # 'company_name': [df[df['Symbol'] == ticker]['Security'].values[0]], # option2 after index reset
#                 'financial_str': [scores['financial_str']],
#                 'profit': [scores['profit']],
#                 'growth': [scores['growth']],
#                 'gf_value': [scores['gf_value']],
#                 'momentum': [scores['momentum']],
#                 'GF_score': [scores['GF_score']],
#
#             })
#             df_best_companies = pd.concat([df_best_companies, new_row], ignore_index=True) # add the current company
#
#     df_best_companies = df_best_companies.sort_values(by='GF_score', ascending=False) # Sort companies by GF Score
#     # df_best_companies.to_csv("/home/nim/best_companies.csv", index=False)
#
#     return df_best_companies
#
# df_best_companies = get_best_companies(tickers_list, df)
#
# # to rename a column
# # df_best_companies.rename(columns={'financial_str_score': 'financial_str'}, inplace=True)





################ New Code:
import numpy as np
import pandas as pd
from typing import List, Optional
import os
import sys

import numpy as np
import pandas as pd

project_root = os.path.abspath("/home/nim/venv/web_stuff")
sys.path.append(project_root)

from investing.gf_analyze_ticker_new import get_financial_data_for_ticker

# -------------------------------
# Helpers
# -------------------------------

def extract_score_int(main_scores: dict, key: str) -> float:
    """
    Convert a score string like '8/10' or '69/100' into an integer (8, 69).
    Return NaN if missing/unparsable.
    """
    try:
        raw = main_scores.get(key, None)
        if not raw:
            return np.nan
        left = str(raw).split('/', 1)[0].strip()
        return int(left)
    except Exception:
        return np.nan


def lookup_company_name(ticker: str, df: Optional[pd.DataFrame]) -> str:
    """
    Return the company name for a ticker, if df is provided and has mapping.
    Otherwise return NaN.
    """
    if df is None:
        return np.nan

    # If ticker is in the index with 'Company' column
    if ticker in df.index and "Company" in df.columns:
        try:
            return str(df.loc[ticker, "Company"])
        except Exception:
            return np.nan

    # If df has 'Symbol' column
    if "Symbol" in df.columns:
        sub = df.loc[df["Symbol"] == ticker]
        if not sub.empty and "Company" in sub.columns:
            return str(sub.iloc[0]["Company"])

    return np.nan


# -------------------------------
# Core
# -------------------------------

def get_best_companies(ticker_list: List[str], df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """
    For each ticker:
      - Fetch GuruFocus scores
      - Extract numeric values
      - Keep tickers with (financial_str >= 8 AND profit >= 8)

    If df is provided and contains company names, include them.
    Otherwise fill 'company_name' with NaN.

    Returns: DataFrame sorted by GF_score desc.
    """
    SCORE_KEYS = ["financial_str", "profit", "growth", "gf_value", "momentum", "GF_score"]
    rows = []

    for i, ticker in enumerate(ticker_list, start=1):
        print(f"i: {i} - ticker: {ticker}")

        try:
            main_scores, _ = get_financial_data_for_ticker(ticker, print_all_data=False)
        except Exception as e:
            print(f"  [WARN] Failed to fetch {ticker}: {e}")
            continue

        # Build numeric score dict
        scores = {k: extract_score_int(main_scores, k) for k in SCORE_KEYS}

        # Rule: must meet thresholds
        if (scores["financial_str"] >= 8) and (scores["profit"] >= 8):
            rows.append({
                "ticker": ticker,
                "company_name": lookup_company_name(ticker, df),
                **scores
            })

    df_best = pd.DataFrame(rows, columns=["ticker", "company_name"] + SCORE_KEYS)

    if not df_best.empty:
        df_best = df_best.sort_values(by="GF_score", ascending=False, na_position="last").reset_index(drop=True)

    return df_best

# --------------------------------------------------------------------------------------
# Example usage
# --------------------------------------------------------------------------------------
tickers_list = ['NVDA', 'MSFT', 'SMR', 'AAPL', 'DOV']
df_best_companies = get_best_companies(tickers_list) # optional (tickers_list, ipos_df)

# # Optional: preview (hide scraped_at or score_ etc. if you add those later)
# print(df_best_companies.head())

