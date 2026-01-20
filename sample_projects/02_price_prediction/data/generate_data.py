"""Generate synthetic house price data."""

import numpy as np
import pandas as pd
from pathlib import Path


def generate_house_data(n_samples=8000, random_state=42):
    """
    Generate synthetic house price data for real estate.

    Args:
        n_samples: Number of house records to generate
        random_state: Random seed for reproducibility

    Returns:
        pd.DataFrame: Generated house data with features and target
    """
    np.random.seed(random_state)

    # Generate property IDs
    property_ids = [f"PROP{i:06d}" for i in range(n_samples)]

    # Generate location features
    neighborhoods = np.random.choice(
        ["Downtown", "Suburbs", "Urban", "Rural", "Waterfront"],
        n_samples,
        p=[0.15, 0.40, 0.25, 0.15, 0.05]
    )

    # Base prices by neighborhood
    base_prices = {
        "Downtown": 400000,
        "Suburbs": 300000,
        "Urban": 350000,
        "Rural": 200000,
        "Waterfront": 600000
    }

    # Generate property characteristics
    square_feet = np.random.normal(2000, 800, n_samples)
    square_feet = np.clip(square_feet, 800, 6000)

    bedrooms = np.random.choice([2, 3, 4, 5, 6], n_samples, p=[0.15, 0.35, 0.30, 0.15, 0.05])
    bathrooms = np.random.choice([1, 1.5, 2, 2.5, 3, 3.5, 4], n_samples,
                                  p=[0.10, 0.15, 0.30, 0.20, 0.15, 0.07, 0.03])

    lot_size = np.random.normal(8000, 4000, n_samples)
    lot_size = np.clip(lot_size, 2000, 30000)

    year_built = np.random.randint(1950, 2024, n_samples)
    age = 2024 - year_built

    # Property type
    property_types = np.random.choice(
        ["Single Family", "Condo", "Townhouse", "Multi-Family"],
        n_samples,
        p=[0.60, 0.20, 0.15, 0.05]
    )

    # Condition and quality
    condition = np.random.choice([1, 2, 3, 4, 5], n_samples, p=[0.05, 0.15, 0.50, 0.25, 0.05])
    quality = np.random.choice([1, 2, 3, 4, 5], n_samples, p=[0.05, 0.20, 0.45, 0.25, 0.05])

    # Features
    has_garage = np.random.choice([0, 1], n_samples, p=[0.20, 0.80])
    garage_spaces = np.where(has_garage, np.random.choice([1, 2, 3], n_samples, p=[0.30, 0.60, 0.10]), 0)

    has_pool = np.random.choice([0, 1], n_samples, p=[0.85, 0.15])
    has_fireplace = np.random.choice([0, 1], n_samples, p=[0.60, 0.40])
    has_basement = np.random.choice([0, 1], n_samples, p=[0.50, 0.50])

    # Renovated
    is_renovated = np.random.choice([0, 1], n_samples, p=[0.70, 0.30])
    years_since_renovation = np.where(is_renovated, np.random.randint(1, 20, n_samples), 0)

    # School rating (1-10)
    school_rating = np.random.choice(range(1, 11), n_samples, p=[0.05, 0.05, 0.08, 0.10, 0.15, 0.20, 0.15, 0.12, 0.07, 0.03])

    # Distance to city center (miles)
    distance_to_center = np.random.gamma(3, 2, n_samples)

    # Calculate price based on realistic factors
    prices = np.zeros(n_samples)

    # Base price by neighborhood
    for hood in base_prices.keys():
        mask = neighborhoods == hood
        prices[mask] = base_prices[hood]

    # Adjust for square footage (primary driver)
    prices += (square_feet - 2000) * 100

    # Adjust for bedrooms and bathrooms
    prices += (bedrooms - 3) * 15000
    prices += (bathrooms - 2) * 20000

    # Adjust for lot size
    prices += (lot_size - 8000) * 5

    # Adjust for age (depreciation for old houses, premium for new)
    prices -= age * 1000
    prices[age > 50] += 20000  # Historic premium

    # Adjust for condition and quality
    prices += (condition - 3) * 20000
    prices += (quality - 3) * 30000

    # Property type adjustments
    prices[property_types == "Condo"] -= 50000
    prices[property_types == "Townhouse"] -= 30000
    prices[property_types == "Multi-Family"] += 40000

    # Features
    prices += has_pool * 40000
    prices += has_fireplace * 15000
    prices += has_basement * 35000
    prices += garage_spaces * 15000

    # Renovation
    prices[is_renovated == 1] += 50000
    prices[is_renovated == 1] -= years_since_renovation[is_renovated == 1] * 2000

    # School rating (very important)
    prices += (school_rating - 5) * 15000

    # Distance to city center
    prices -= distance_to_center * 8000

    # Add some noise
    noise = np.random.normal(0, 30000, n_samples)
    prices += noise

    # Ensure positive prices
    prices = np.maximum(prices, 50000)

    # Create DataFrame
    data = pd.DataFrame({
        'property_id': property_ids,
        'neighborhood': neighborhoods,
        'square_feet': square_feet.astype(int),
        'bedrooms': bedrooms,
        'bathrooms': bathrooms,
        'lot_size': lot_size.astype(int),
        'year_built': year_built,
        'age': age,
        'property_type': property_types,
        'condition': condition,
        'quality': quality,
        'has_garage': has_garage,
        'garage_spaces': garage_spaces,
        'has_pool': has_pool,
        'has_fireplace': has_fireplace,
        'has_basement': has_basement,
        'is_renovated': is_renovated,
        'years_since_renovation': years_since_renovation,
        'school_rating': school_rating,
        'distance_to_center': distance_to_center.round(2),
        'price': prices.astype(int)
    })

    return data


def save_data(data, output_dir='data'):
    """
    Save generated data to CSV file.

    Args:
        data: DataFrame with house data
        output_dir: Directory to save the data
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    file_path = output_path / 'house_prices.csv'
    data.to_csv(file_path, index=False)
    print(f"Data saved to {file_path}")
    print(f"Total samples: {len(data)}")
    print(f"Price range: ${data['price'].min():,.0f} - ${data['price'].max():,.0f}")
    print(f"Mean price: ${data['price'].mean():,.0f}")


if __name__ == "__main__":
    # Generate data
    print("Generating synthetic house price data...")
    df = generate_house_data(n_samples=8000, random_state=42)

    # Save data
    save_data(df, output_dir='data')

    # Print summary statistics
    print("\nDataset summary:")
    print(df[['square_feet', 'bedrooms', 'bathrooms', 'age', 'price']].describe())
