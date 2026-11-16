import os
from image_lab import create_app
from loomlab import install
from waitress import serve
root = os.environ.get('LOOMLAB_DATA_DIR','data')
app = create_app(os.path.join(root,'preparation'))
install(app,os.path.join(root,'materials'))
if __name__=='__main__':
    serve(app,host=os.environ.get('HOST','0.0.0.0'),port=int(os.environ.get('PORT','18915')),threads=4)
