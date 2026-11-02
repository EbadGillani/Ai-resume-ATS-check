# AI Resume ATS Checker

A Streamlit app that reads a resume (PDF, DOCX or TXT), optionally compares it with a job description, and uses Google's Gemini Flash model to return:

- an estimated **ATS score** (0-100) with a five-part breakdown
- strengths, **missing keywords** and formatting/parsing issues
- **prioritised improvements** and sample bullet-point rewrites
- a downloadable JSON report

> No real ATS publishes a universal score. The number is an AI-based estimate of parseability and keyword match, meant as guidance, not a guarantee.

## How the score works

The model rates five categories from 0-100, and the app computes the overall score itself using fixed weights so it stays consistent:

| Category | Weight |
|---|---|
| Keywords & Skills | 30% |
| Impact & Achievements | 25% |
| Formatting & ATS Parseability | 20% |
| Structure & Sections | 15% |
| Language & Readability | 10% |

## Project files

```
app.py            # Streamlit app
requirements.txt  # Python dependencies
README.md         # This file
```

## Run locally

1. Get a free Gemini API key at https://aistudio.google.com/apikey
2. Create a virtual environment and install dependencies:

   ```bash
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. Provide your key (pick one):

   ```bash
   export GEMINI_API_KEY="your-key"          # Windows PowerShell: $env:GEMINI_API_KEY="your-key"
   ```

   or create `.streamlit/secrets.toml`:

   ```toml
   GEMINI_API_KEY = "your-key"
   ```

   or just paste the key into the app's sidebar.

4. Start the app:

   ```bash
   streamlit run app.py
   ```

Optional: set `GEMINI_MODEL` (default `gemini-2.5-flash`) or change the model name in the sidebar.

## Push to GitHub

1. Create an empty repository on https://github.com/new (no README, no .gitignore).
2. In your project folder, protect your secrets first:

   ```bash
   printf ".venv/\n__pycache__/\n.streamlit/secrets.toml\n.env\n" > .gitignore
   ```

3. Initialise, commit and push (replace `YOUR_USERNAME` and `YOUR_REPO`):

   ```bash
   git init
   git add app.py requirements.txt README.md .gitignore
   git commit -m "Initial commit: AI Resume ATS Checker"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
   git push -u origin main
   ```

   When asked for a password, use a GitHub **personal access token** (Settings > Developer settings > Personal access tokens), or sign in with the GitHub CLI (`gh auth login`).

Never commit your API key.

## Deploy on Streamlit Community Cloud (free)

1. Go to https://share.streamlit.io and sign in with GitHub.
2. Click **Create app** > **Deploy a public app from GitHub**.
3. Choose your repository, branch `main`, and main file path `app.py`.
4. Open **Advanced settings** > **Secrets** and paste:

   ```toml
   GEMINI_API_KEY = "your-key"
   ```

5. (Optional) Pick a Python version (3.11 or 3.12 recommended) and click **Deploy**.

After a minute or two you get a public URL. Every `git push` to `main` redeploys automatically. To change the key later, use the app's **Settings > Secrets**.

If you would rather not spend your own quota on public visitors, leave the secret out: the app then asks each visitor for their own key in the sidebar.

## Troubleshooting

- **"Not enough readable text"**: the PDF is probably a scanned image. Export a text-based PDF or use DOCX.
- **Model not found (404)**: Gemini model names change. Update the name in the sidebar or via `GEMINI_MODEL`.
- **Rate limit (429)**: the free tier has per-minute and daily limits; wait and retry.
- **Privacy**: resume text is sent to the Gemini API for analysis. Do not use real personal data you are not comfortable sharing, and review Google's API data terms.
