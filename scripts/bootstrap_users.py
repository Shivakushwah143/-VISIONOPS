import argparse,getpass
from backend.app.db import Session,User
from backend.app.security import passwords
p=argparse.ArgumentParser();p.add_argument('--email',required=True);p.add_argument('--role',choices=['mlops_engineer','fleet_operator','cv_engineer','safety_viewer'],default='mlops_engineer');a=p.parse_args()
pwd=getpass.getpass('New password (minimum 12 characters): ')
if len(pwd)<12:raise SystemExit('Password too short')
with Session() as db:db.add(User(email=a.email,password_hash=passwords.hash(pwd),role=a.role));db.commit()
print('User created')
