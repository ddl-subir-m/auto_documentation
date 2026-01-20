"""Generate synthetic customer churn data."""

import numpy as np
import pandas as pd
from pathlib import Path


def generate_customer_data(n_samples=10000, random_state=42, churn_rate=0.15):
    """
    Generate synthetic customer churn data for telecom company.

    Args:
        n_samples: Number of customer records to generate
        random_state: Random seed for reproducibility
        churn_rate: Target churn rate (proportion of churned customers)

    Returns:
        pd.DataFrame: Generated customer data with features and target
    """
    np.random.seed(random_state)

    # Generate customer IDs
    customer_ids = [f"CUST{i:06d}" for i in range(n_samples)]

    # Generate tenure (months with company)
    tenure_months = np.random.randint(1, 73, n_samples)

    # Generate contract types with realistic distribution
    contract_types = np.random.choice(
        ["Month-to-month", "One year", "Two year"],
        n_samples,
        p=[0.55, 0.25, 0.20]
    )

    # Generate payment methods
    payment_methods = np.random.choice(
        ["Electronic check", "Mailed check", "Bank transfer", "Credit card"],
        n_samples,
        p=[0.35, 0.20, 0.25, 0.20]
    )

    # Generate internet service types
    internet_services = np.random.choice(
        ["DSL", "Fiber optic", "No"],
        n_samples,
        p=[0.35, 0.45, 0.20]
    )

    # Generate demographics
    senior_citizen = np.random.choice([0, 1], n_samples, p=[0.85, 0.15])
    partner = np.random.choice([0, 1], n_samples, p=[0.52, 0.48])
    dependents = np.random.choice([0, 1], n_samples, p=[0.70, 0.30])

    # Generate services
    phone_service = np.random.choice([0, 1], n_samples, p=[0.10, 0.90])
    multiple_lines = np.where(phone_service == 1,
                              np.random.choice([0, 1], n_samples, p=[0.48, 0.52]),
                              0)

    online_security = np.where(internet_services != "No",
                               np.random.choice([0, 1], n_samples, p=[0.50, 0.50]),
                               0)
    online_backup = np.where(internet_services != "No",
                             np.random.choice([0, 1], n_samples, p=[0.56, 0.44]),
                             0)
    device_protection = np.where(internet_services != "No",
                                 np.random.choice([0, 1], n_samples, p=[0.56, 0.44]),
                                 0)
    tech_support = np.where(internet_services != "No",
                            np.random.choice([0, 1], n_samples, p=[0.51, 0.49]),
                            0)
    streaming_tv = np.where(internet_services != "No",
                           np.random.choice([0, 1], n_samples, p=[0.52, 0.48]),
                           0)
    streaming_movies = np.where(internet_services != "No",
                                np.random.choice([0, 1], n_samples, p=[0.52, 0.48]),
                                0)

    # Generate paperless billing
    paperless_billing = np.random.choice([0, 1], n_samples, p=[0.41, 0.59])

    # Generate monthly charges based on services
    base_charge = 20.0
    monthly_charges = base_charge + np.random.uniform(0, 10, n_samples)

    # Add charges for services
    monthly_charges += phone_service * np.random.uniform(15, 25, n_samples)
    monthly_charges += multiple_lines * np.random.uniform(5, 15, n_samples)

    # Internet service charges
    internet_charge = np.zeros(n_samples)
    internet_charge[internet_services == "DSL"] = np.random.uniform(25, 35, sum(internet_services == "DSL"))
    internet_charge[internet_services == "Fiber optic"] = np.random.uniform(50, 80, sum(internet_services == "Fiber optic"))
    monthly_charges += internet_charge

    # Add-on services
    monthly_charges += online_security * np.random.uniform(5, 10, n_samples)
    monthly_charges += online_backup * np.random.uniform(5, 10, n_samples)
    monthly_charges += device_protection * np.random.uniform(5, 10, n_samples)
    monthly_charges += tech_support * np.random.uniform(5, 10, n_samples)
    monthly_charges += streaming_tv * np.random.uniform(8, 12, n_samples)
    monthly_charges += streaming_movies * np.random.uniform(8, 12, n_samples)

    # Calculate total charges
    total_charges = monthly_charges * tenure_months
    total_charges += np.random.uniform(-50, 50, n_samples)  # Add some noise

    # Calculate churn probability based on realistic factors
    churn_probability = np.full(n_samples, 0.10)  # Base rate

    # Increase churn for month-to-month contracts
    churn_probability += (contract_types == "Month-to-month") * 0.20

    # Increase churn for electronic check payment
    churn_probability += (payment_methods == "Electronic check") * 0.08

    # Increase churn for short tenure
    churn_probability += (tenure_months < 6) * 0.15
    churn_probability += (tenure_months < 12) * 0.08

    # Increase churn for senior citizens
    churn_probability += senior_citizen * 0.05

    # Increase churn for fiber optic (possibly due to higher cost)
    churn_probability += (internet_services == "Fiber optic") * 0.06

    # Decrease churn for customers with dependents or partners
    churn_probability -= dependents * 0.05
    churn_probability -= partner * 0.03

    # Decrease churn for customers with add-on services (more engaged)
    churn_probability -= (online_security + online_backup + device_protection + tech_support) * 0.02

    # Decrease churn for long-term contracts
    churn_probability -= (contract_types == "Two year") * 0.15

    # Ensure probability is between 0 and 1
    churn_probability = np.clip(churn_probability, 0.0, 1.0)

    # Adjust to match target churn rate
    adjustment = churn_rate / np.mean(churn_probability)
    churn_probability *= adjustment
    churn_probability = np.clip(churn_probability, 0.0, 1.0)

    # Generate churn target
    churned = np.random.binomial(1, churn_probability)

    # Create DataFrame
    data = pd.DataFrame({
        'customer_id': customer_ids,
        'senior_citizen': senior_citizen,
        'partner': partner,
        'dependents': dependents,
        'tenure_months': tenure_months,
        'phone_service': phone_service,
        'multiple_lines': multiple_lines,
        'internet_service': internet_services,
        'online_security': online_security,
        'online_backup': online_backup,
        'device_protection': device_protection,
        'tech_support': tech_support,
        'streaming_tv': streaming_tv,
        'streaming_movies': streaming_movies,
        'contract_type': contract_types,
        'paperless_billing': paperless_billing,
        'payment_method': payment_methods,
        'monthly_charges': monthly_charges,
        'total_charges': total_charges,
        'churned': churned
    })

    return data


def save_data(data, output_dir='data'):
    """
    Save generated data to CSV file.

    Args:
        data: DataFrame with customer data
        output_dir: Directory to save the data
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    file_path = output_path / 'customer_churn.csv'
    data.to_csv(file_path, index=False)
    print(f"Data saved to {file_path}")
    print(f"Total samples: {len(data)}")
    print(f"Churn rate: {data['churned'].mean():.2%}")


if __name__ == "__main__":
    # Generate data
    print("Generating synthetic customer churn data...")
    df = generate_customer_data(n_samples=10000, random_state=42, churn_rate=0.15)

    # Save data
    save_data(df, output_dir='data')

    # Print summary statistics
    print("\nDataset summary:")
    print(df.describe())
    print("\nChurn distribution:")
    print(df['churned'].value_counts())
