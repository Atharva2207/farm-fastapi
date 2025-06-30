# Start the application

- Make you have python 3.12+ installed

```bash
# create virtual envirionment
python3 -m venv venv

# activat the envirionment
source venv/bin/activate

# install dependencies
pip install -r requirements.txt

# run dev code
cd codebase
uvicorn main:app --port 8000 --reload

# Make sure settings are added .env
# Running alembic migrations
cd code
alembic upgrade heads

```