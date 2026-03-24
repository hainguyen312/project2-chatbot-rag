from peewee import MySQLDatabase

DATABASE = 'mysql://root:123456789@127.0.0.1:3306/law'
db = MySQLDatabase(
    database='law',
    user='root',
    password='123456789',
    host='127.0.0.1',
    port=3306,
)


