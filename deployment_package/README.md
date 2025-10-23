# Shopify + Dropbox Scanner Integration

Automatically routes Noritsu scans to customer Dropbox folders and manages Shopify orders.

## What it does

1. **Watches** your scanner output for completed scans
2. **Prompts** you to search and select the correct Shopify order  
3. **Uploads** photos to the right customer folder automatically
4. **Saves** customer Dropbox links to their Shopify profiles
5. **Tags** orders to trigger your Shopify Flow

## Folder Structure

```
/Store/orders/
├── JeffS@gmail.com/
│   └── 100/
│       ├── 2323/photos/  (roll 1)
│       └── 2324/photos/  (roll 2)
└── _staging/  (for uncertain orders)
```

## Quick Setup

1. **Install**:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure**:
   ```bash
   cp env_template.txt .env
   # Edit .env with your credentials
   ```

3. **Run**:
   ```bash
   python scanner_router.py
   ```

## Required Setup

### Shopify
1. Create custom app in Shopify Admin
2. Enable permissions: `read_orders`, `write_orders`, `read_customers`, `write_customers`
3. Create customer metafield: `custom.dropbox_root_url` (URL type)
4. Update your Flow to use: `{{ order.customer.metafields.custom.dropbox_root_url }}`

### Dropbox  
1. Create app at [developers.dropbox.com](https://developers.dropbox.com)
2. Enable: `files.content.write`, `files.content.read`, `files.metadata.read`, `files.metadata.write`, `sharing.read`, `sharing.write`
3. Generate access token for `/Store/orders` account

### Scanner Computer
1. Install Python
2. Copy app files to scanner PC
3. Configure `.env` file

## How to Use

1. **Start**: `python scanner_router.py`
2. **Scan film** - app watches for completed folders
3. **When ready**, type search term:
   - Email: `jeff@example.com`
   - Order #: `100` 
   - Name: `jeff`
   - Defer: `stage`
4. **Pick from matches** and the app handles the rest

### Staging
- Type `stage` if unsure about an order
- Later run: `python reassign_staged.py`

## Files

- `scanner_router.py` - Main app (watches scanner, uploads to Dropbox)
- `reassign_staged.py` - Move staged orders to customers  
- `env_template.txt` - Copy to `.env` and add your credentials
- `requirements.txt` - Python dependencies

## Troubleshooting

- **"Missing .env entries"** - Check all required variables are set
- **"Noritsu root not found"** - Verify `NORITSU_ROOT` path is correct
- **"Shopify GraphQL error"** - Check token permissions and store name
- **"Could not save customer link"** - Verify customer metafield is configured
