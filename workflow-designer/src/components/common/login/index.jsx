import {useState, useEffect} from "react";
import {useTranslation} from "react-i18next";
import {Lock, ArrowRight, AlertCircle, Loader2, Settings} from "lucide-react";
import {User} from "lucide-react";
import SettingsModal from "../setting/index.jsx";
import openanLogo from "../../../assets/openan-logo.png";
import {login, register} from "@/service/api.js";

const Login = ({isDark, onLoginSuccess, onLoginWithUser, registrationEnabled}) => {
const {t, i18n} = useTranslation();
    const [username, setUsername] = useState("admin");
    const [password, setPassword] = useState("");
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(false);
    const [isSettingsOpen, setIsSettingsOpen] = useState(false);
    const [isRegisterMode, setIsRegisterMode] = useState(false);
    const [confirmPassword, setConfirmPassword] = useState("");

    useEffect(() => {
        const lang = localStorage.getItem('lang') || 'en';
        if (lang !== i18n.language) {
            i18n.changeLanguage(lang);
        }
    }, []);

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!password.trim() || loading) return;
        setLoading(true);
        setError("");
        try {
           const data = await login(username, password);
           if (data && data.token) {
                onLoginWithUser ? onLoginWithUser(data.username) : onLoginSuccess();
           } else if (data && data.auth_required === false) {
               onLoginSuccess();
           }
        } catch (err) {
            const msg = err?.response?.data?.message || err?.message || "";
            setError(msg || t('login.error'));
        } finally {
            setLoading(false);
        }
    };

   const handleRegister = async (e) => {
       e.preventDefault();
       if (!username.trim() || !password.trim() || loading) return;
       if (password.length < 8) {
           setError(t('login.password_too_short'));
           return;
       }
       if (!/[A-Z]/.test(password) || !/[a-z]/.test(password) || !/[0-9]/.test(password)) {
           setError(t('login.password_complexity'));
           return;
       }
       if (password !== confirmPassword) {
            setError(t('login.password_mismatch'));
            return;
        }
        setLoading(true);
        setError("");
        try {
            await register(username, password);
            const data = await login(username, password);
           if (data && data.token) {
                onLoginWithUser ? onLoginWithUser(data.username) : onLoginSuccess();
            } 
       } catch (err) {
            const msg = err?.response?.data?.message || err?.message || "";
            setError(msg || t('login.error'));
        } finally {
            setLoading(false);
        }
    };

    const handleLangChange = (l) => {
        i18n.changeLanguage(l);
        localStorage.setItem('lang', l);
    };

    return (
        <div className="h-screen flex items-center justify-center bg-zinc-50 dark:bg-[#09090B] font-sans transition-colors duration-500">
            <div className="w-full max-w-md px-8">
                <div className="flex flex-col items-center mb-10">
                    <div className="w-16 h-16 rounded-xl bg-white p-1.5 flex items-center justify-center shadow-lg mb-4">
                        <img src={openanLogo} alt="OpenAN" className="w-full h-full object-contain"/>
                    </div>
                    <h1 className="text-3xl font-medium tracking-tight text-zinc-900 dark:text-zinc-100">
                        Open<span className="text-blue-500">AN</span>
                    </h1>
                    <p className="text-xs tracking-widest uppercase text-zinc-400 dark:text-zinc-500 mt-1">
                        Open Autonomous Networks
                    </p>
                </div>

                <form onSubmit={isRegisterMode ? handleRegister : handleSubmit}
                      className="bg-white dark:bg-zinc-900 rounded-2xl border border-zinc-200 dark:border-zinc-800 shadow-xl p-8 space-y-5">
                    <div className="flex items-center gap-2 text-zinc-700 dark:text-zinc-200">
                        <Lock size={18} className="text-blue-500"/>
                        <span className="text-sm font-medium">{isRegisterMode ? t('login.register_title') : t('login.title')}</span>
                   </div>

                    <div className="relative">
                        <input
                            type="text"
                            value={username}
                            onChange={(e) => setUsername(e.target.value)}
                            placeholder={t('login.username_placeholder')}
                            disabled={loading}
                            className="w-full pl-10 pr-4 py-3 rounded-xl bg-zinc-50 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 dark:placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-all text-sm"
                        />
                        <User size={16} className="absolute left-3.5 top-3.5 text-zinc-400"/>
                    </div>

                    <div>
                        <input
                            type="password"
                            value={password}
                            onChange={(e) => setPassword(e.target.value)}
                            placeholder={t('login.placeholder')}
                            disabled={loading}
                            className="w-full px-4 py-3 rounded-xl bg-zinc-50 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 dark:placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-all text-sm"
                        />
                    </div>

                    {isRegisterMode && (
                    <div className="relative">
                        <input
                            type="password"
                            value={confirmPassword}
                            onChange={(e) => setConfirmPassword(e.target.value)}
                            placeholder={t('login.confirm_password')}
                            disabled={loading}
                            className="w-full pl-10 pr-4 py-3 rounded-xl bg-zinc-50 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 dark:placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-all text-sm"
                        />
                        <Lock size={16} className="absolute left-3.5 top-3.5 text-zinc-400"/>
                    </div>
                    )}

                    {error && (
                        <div className="flex items-center gap-2 text-sm text-red-500">
                            <AlertCircle size={16}/>
                            <span>{error}</span>
                        </div>
                    )}

                    <button
                        type="submit"
                        disabled={loading || !password.trim()}
                        className="w-full flex items-center justify-center gap-2 px-4 py-3 rounded-xl bg-blue-500 hover:bg-blue-600 disabled:opacity-40 disabled:cursor-not-allowed text-white font-medium text-sm transition-all">
                        {loading ? (
                            <>
                                <Loader2 size={18} className="animate-spin"/>
                                {t('login.loading')}
                            </>
                        ) : (
                            <>
                                {isRegisterMode ? t('login.register_button') : t('login.button')}
                                <ArrowRight size={18}/>
                            </>
                        )}
                    </button>

                    {registrationEnabled && (
                    <div className="text-center text-sm text-zinc-400">
                        {isRegisterMode ? (
                            <span>{t('login.have_account')} <button type="button" onClick={() => {setIsRegisterMode(false); setError("");}} className="text-blue-500 hover:underline font-medium">{t('login.button')}</button></span>
                        ) : (
                            <span>{t('login.no_account')} <button type="button" onClick={() => {setIsRegisterMode(true); setError("");}} className="text-blue-500 hover:underline font-medium">{t('login.register_button')}</button></span>
                        )}
                    </div>
                    )}
                </form>

                <div className="flex justify-center mt-6 gap-4 items-center">
                    <div className="flex bg-zinc-100 dark:bg-zinc-800 p-1 rounded-full border border-zinc-200 dark:border-zinc-700 shadow-inner">
                        <button onClick={() => handleLangChange('zh')}
                                className={`px-4 py-1.5 rounded-full text-xs font-black transition-all ${i18n.language === 'zh' ? 'bg-white dark:bg-zinc-600 text-blue-600 dark:text-white shadow-sm' : 'text-zinc-400'}`}>
                            中
                        </button>
                        <button onClick={() => handleLangChange('en')}
                                className={`px-4 py-1.5 rounded-full text-xs font-black transition-all ${i18n.language === 'en' ? 'bg-white dark:bg-zinc-600 text-blue-600 dark:text-white shadow-sm' : 'text-zinc-400'}`}>
                            EN
                        </button>
                    </div>
                    <button onClick={() => setIsSettingsOpen(true)}
                            className="p-2.5 rounded-full hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-all">
                        <Settings size={20} className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 transition-all"/>
                    </button>
                </div>
            </div>
            <SettingsModal isOpen={isSettingsOpen} onClose={() => setIsSettingsOpen(false)} t={t}/>
        </div>
    );
};

export default Login;
