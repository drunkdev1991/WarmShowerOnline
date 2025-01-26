FROM python:3.10-alpine

WORKDIR /app

COPY . /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r ./requirements.txt

# frontend
RUN apk add --no-cache nodejs npm
RUN npm install --prefix ./frontend
RUN npm run build --prefix ./frontend

# copy internal frontend build to flask static folder
RUN cp -r frontend/dist /app/static
RUN ls /app/static

EXPOSE 5000

CMD ["python", "app.py"]