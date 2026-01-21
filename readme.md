python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt

python main.py setup

#run repo
python main.py connect https://github.com/SamuelHanono/sherif-tester

./venv/bin/python main.py connect https://github.com/SamuelHanono/sherif-tester