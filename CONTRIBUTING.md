# Contributing to StreamForge

Thanks for your interest in contributing! 🎉

## Quick Start

```bash
# Fork & clone
git clone https://github.com/YOUR_USERNAME/streamforge.git
cd streamforge

# Install dependencies
pip install -r requirements.txt

# Run dev server
uvicorn app.main:app --reload --port 8000

# Run tests
pytest tests/ -v
```

## How to Contribute

### 🐛 Bug Reports
Open an issue with:
- Steps to reproduce
- Expected vs actual behavior
- OS, Python version, FFmpeg version

### ✨ Feature Requests
Open an issue describing:
- The problem you're trying to solve
- Your proposed solution
- Any alternatives you've considered

### 🔧 Pull Requests

1. Fork the repo and create your branch from `main`
2. Write clear, concise commit messages
3. Add tests for new features
4. Ensure all tests pass: `pytest tests/ -v`
5. Update documentation if needed
6. Open a PR with a clear description

## Code Style

- Python: Follow PEP 8, use type hints where possible
- JavaScript: Use modern ES6+ syntax
- CSS: Use CSS custom properties (variables)
- Keep functions small and focused

## Project Structure

```
app/
├── main.py          # FastAPI app + middleware
├── routes.py        # API endpoints
├── processor.py     # FFmpeg engine
├── hardware.py      # Hardware detection
├── storage.py       # R2 integration
├── config.py        # Configuration
├── middleware.py     # Auth + rate limiting
├── metrics.py       # Prometheus
└── webhook.py       # Notifications
```

## Areas We Need Help

- 🌐 Multi-language UI support
- 🎨 Mobile-responsive improvements
- 📊 Dashboard for processing analytics
- 🧪 More comprehensive tests
- 📚 Video tutorials and guides
- 🐳 Kubernetes deployment configs

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
