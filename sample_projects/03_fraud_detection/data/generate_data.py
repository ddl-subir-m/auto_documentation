"""Generate synthetic credit card fraud data."""

import numpy as np
import pandas as pd
from pathlib import Path


def generate_fraud_data(n_samples=50000, random_state=42, fraud_rate=0.02):
    """
    Generate synthetic credit card transaction data with fraud labels.

    Args:
        n_samples: Number of transaction records to generate
        random_state: Random seed for reproducibility
        fraud_rate: Target fraud rate (proportion of fraudulent transactions)

    Returns:
        pd.DataFrame: Generated transaction data with features and fraud label
    """
    np.random.seed(random_state)

    # Generate transaction IDs
    transaction_ids = [f"TXN{i:08d}" for i in range(n_samples)]

    # Time features
    hour_of_day = np.random.randint(0, 24, n_samples)
    day_of_week = np.random.randint(0, 7, n_samples)

    # Transaction amount (log-normal distribution)
    amount = np.random.lognormal(mean=4, sigma=1.5, size=n_samples)
    amount = np.clip(amount, 1, 10000)

    # Merchant category
    merchant_categories = np.random.choice(
        ["Retail", "Restaurant", "Gas Station", "Online", "Travel", "Entertainment", "Grocery"],
        n_samples,
        p=[0.20, 0.15, 0.12, 0.18, 0.10, 0.10, 0.15]
    )

    # Location features
    distance_from_home = np.random.gamma(2, 10, n_samples)
    is_foreign = np.random.choice([0, 1], n_samples, p=[0.95, 0.05])

    # Card features
    card_age_days = np.random.randint(0, 3650, n_samples)

    # User behavior
    avg_transaction_amount = np.random.lognormal(mean=4, sigma=0.8, size=n_samples)
    transactions_last_24h = np.random.poisson(2, n_samples)
    transactions_last_week = np.random.poisson(15, n_samples)

    # Device/channel
    is_online = np.random.choice([0, 1], n_samples, p=[0.60, 0.40])
    device_types = np.random.choice(["Desktop", "Mobile", "Tablet", "In-Person"], n_samples,
                                    p=[0.15, 0.25, 0.05, 0.55])

    # Merchant trust score (1-10)
    merchant_trust = np.random.choice(range(1, 11), n_samples,
                                      p=[0.02, 0.03, 0.05, 0.08, 0.12, 0.18, 0.20, 0.15, 0.10, 0.07])

    # Previous fraud history
    account_age_months = np.random.randint(1, 120, n_samples)
    previous_fraud_count = np.random.choice([0, 0, 0, 0, 0, 0, 0, 0, 1, 2], n_samples)

    # Ratio features
    amount_to_avg_ratio = amount / (avg_transaction_amount + 1)

    # Velocity features
    daily_transaction_velocity = transactions_last_24h / 1.0  # transactions per hour
    weekly_transaction_velocity = transactions_last_week / 7.0  # transactions per day

    # Time since last transaction
    time_since_last_txn = np.random.exponential(scale=12, size=n_samples)  # hours

    # Card present indicator
    card_present = np.where(device_types == "In-Person", 1, 0)

    # PIN used
    pin_used = np.random.choice([0, 1], n_samples, p=[0.60, 0.40])

    # Calculate fraud probability based on realistic risk factors
    fraud_probability = np.full(n_samples, 0.005)  # Base rate

    # High-risk factors (increase fraud probability)
    fraud_probability += (amount > 1000) * 0.03
    fraud_probability += (amount > 3000) * 0.05
    fraud_probability += is_foreign * 0.04
    fraud_probability += (distance_from_home > 100) * 0.03
    fraud_probability += (merchant_categories == "Online") * 0.015
    fraud_probability += (hour_of_day >= 23) * 0.02  # Late night
    fraud_probability += (hour_of_day <= 5) * 0.02   # Early morning
    fraud_probability += (merchant_trust <= 3) * 0.04
    fraud_probability += (transactions_last_24h > 5) * 0.03
    fraud_probability += (amount_to_avg_ratio > 5) * 0.05
    fraud_probability += (card_age_days < 30) * 0.02  # New cards
    fraud_probability += (previous_fraud_count > 0) * 0.06
    fraud_probability += (is_online == 1) * (1 - card_present) * 0.02

    # Protective factors (decrease fraud probability)
    fraud_probability -= pin_used * 0.01
    fraud_probability -= (merchant_trust >= 8) * 0.005
    fraud_probability -= (account_age_months > 60) * 0.005
    fraud_probability -= card_present * 0.01

    # Ensure probability is between 0 and 1
    fraud_probability = np.clip(fraud_probability, 0.0, 1.0)

    # Adjust to match target fraud rate
    adjustment = fraud_rate / np.mean(fraud_probability)
    fraud_probability *= adjustment
    fraud_probability = np.clip(fraud_probability, 0.0, 1.0)

    # Generate fraud labels
    is_fraud = np.random.binomial(1, fraud_probability)

    # Create DataFrame
    data = pd.DataFrame({
        'transaction_id': transaction_ids,
        'amount': amount.round(2),
        'merchant_category': merchant_categories,
        'hour_of_day': hour_of_day,
        'day_of_week': day_of_week,
        'is_online': is_online,
        'device_type': device_types,
        'card_present': card_present,
        'pin_used': pin_used,
        'distance_from_home': distance_from_home.round(2),
        'is_foreign': is_foreign,
        'merchant_trust': merchant_trust,
        'card_age_days': card_age_days,
        'account_age_months': account_age_months,
        'avg_transaction_amount': avg_transaction_amount.round(2),
        'transactions_last_24h': transactions_last_24h,
        'transactions_last_week': transactions_last_week,
        'amount_to_avg_ratio': amount_to_avg_ratio.round(3),
        'daily_transaction_velocity': daily_transaction_velocity.round(3),
        'weekly_transaction_velocity': weekly_transaction_velocity.round(3),
        'time_since_last_txn': time_since_last_txn.round(2),
        'previous_fraud_count': previous_fraud_count,
        'is_fraud': is_fraud
    })

    return data


def save_data(data, output_dir='data'):
    """
    Save generated data to CSV file.

    Args:
        data: DataFrame with transaction data
        output_dir: Directory to save the data
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    file_path = output_path / 'fraud_transactions.csv'
    data.to_csv(file_path, index=False)
    print(f"Data saved to {file_path}")
    print(f"Total samples: {len(data)}")
    print(f"Fraud rate: {data['is_fraud'].mean():.2%}")
    print(f"Fraudulent transactions: {data['is_fraud'].sum()}")


if __name__ == "__main__":
    # Generate data
    print("Generating synthetic fraud detection data...")
    df = generate_fraud_data(n_samples=50000, random_state=42, fraud_rate=0.02)

    # Save data
    save_data(df, output_dir='data')

    # Print summary statistics
    print("\nDataset summary:")
    print(df[['amount', 'distance_from_home', 'merchant_trust', 'is_fraud']].describe())
    print("\nFraud distribution:")
    print(df['is_fraud'].value_counts())
