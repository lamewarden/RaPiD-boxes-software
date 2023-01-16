# import required module
import os
# assign directory
directory = 'files'

# iterate over files in
# that directory

for file in os.listdir():
    f = os.path.join(os.getcwd(), file)
    if f[-3:] == 'jpg':
        print(f)