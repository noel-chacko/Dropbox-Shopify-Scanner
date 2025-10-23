# Quick Start Guide - Scanner Router

## 🚀 Installation

1. **Install Python** (if not already installed)
2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

## ⚙️ Configuration

1. **Copy the template**:
   ```bash
   copy env_template.txt .env
   ```

2. **Edit .env** with your credentials:
   - Get Shopify token from: Settings → Apps → Develop apps
   - Get Dropbox token from: developers.dropbox.com
   - Set NORITSU_ROOT to your scanner path

## 🧪 Test Setup

```bash
python test_environment.py
```

Should show all green checkmarks.

## 🎬 Run Scanner Router

```bash
python scanner_router.py
```

## 📁 How It Works

1. **Watches** your scanner folder for new scans
2. **Prompts** you to search Shopify for the order
3. **Uploads** photos to customer's Dropbox folder
4. **Updates** Shopify with customer's Dropbox link

## 🔧 Troubleshooting

- **"Path not found"**: Check NORITSU_ROOT path
- **"API errors"**: Verify Shopify/Dropbox tokens
- **"Permission denied"**: Run as administrator if needed

## 📞 Support

Check README.md for detailed setup instructions.
