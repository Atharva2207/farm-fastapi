# Use the official Python image as a parent image
FROM python:3.12

# Install Git
RUN apt-get update && apt-get install -y git

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
COPY ./codebase .

# Copy override the env file
# TODO: Update this to .env
COPY ./codebase/.env .env

# Runs the migrations and start the server
CMD alembic upgrade heads && uvicorn main:app --host 0.0.0.0 --port 8000