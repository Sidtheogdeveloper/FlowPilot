import React from 'react';
import {createRoot} from 'react-dom/client';
import {AccountApp} from './AccountApp';
import {AppearanceProvider} from './AppearanceControls';
import './style.css';
import './product.css';
import './tools.css';
createRoot(document.getElementById('root')!).render(<React.StrictMode><AppearanceProvider><AccountApp/></AppearanceProvider></React.StrictMode>);
