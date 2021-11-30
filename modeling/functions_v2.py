## load modules
import numpy as np
import pandas as pd
from sklearn.metrics import mean_squared_error
from sklearn.model_selection import GridSearchCV
from sklearn.base import clone
import warnings
import mlflow
from copy import deepcopy

from modeling.config import EXPERIMENT_NAME
TRACKING_URI = open("../.mlflow_uri").read().strip()

warnings.filterwarnings('ignore')

# track to MLFLow 
def log_to_mlflow(
        ZONEID=None, Model=None, features=None, train_RMSE=None, test_RMSE=None, 
        nan_removed=False, zero_removed=False, mean=None, 
        hyperparameter=None, model_parameters=None, scaler=None, info=None):
    '''Logs to mlflow. 
    Parameters to log:
    - ZONEID: int
    - Model: string
    - train-RMSE: float
    - test-RMSE: float
    - NaN removed: bool
    - Zero removed: bool
    - statistics: 
        - mean: int
    - hyperparameter: dict
    - model_parameters: dict
    '''

    mlflow.set_tracking_uri(TRACKING_URI)
    mlflow.set_experiment(EXPERIMENT_NAME)

    params = {}
    params['Missing Value Handling'] = { 
        "nan_removed": nan_removed, 
        "zero_removed": zero_removed
    }
    if model_parameters:
        params['model parameters'] = model_parameters
    if hyperparameter:
        params['hyperparameter'] = hyperparameter
            
    mlflow.start_run()
    run = mlflow.active_run()
    print("\nActive run_id: {}".format(run.info.run_id))

    if ZONEID:
        mlflow.set_tag("ZONEID", ZONEID)
    if Model:
        mlflow.set_tag("Model", Model)
    if features:
        mlflow.set_tag("features", features)
        mlflow.set_tag("n_features", len(features))
    if scaler:
        mlflow.set_tag("scaler", scaler.__class__.__name__)
    if train_RMSE:
        mlflow.log_metric("train-RMSE", train_RMSE)
    if test_RMSE:
        mlflow.log_metric("test-RMSE", test_RMSE)
    if mean:
        mlflow.set_tag("mean", mean)
    if params:
        mlflow.log_params(params)
    if info:
        mlflow.set_tag("info", info)

    mlflow.end_run()

    return None


## function for modelling
def modelling(data_train, data_test, features, model, scaler=None, print_scores=True, log=None, infotext_mlflow=None, save_models = False, perform_gridCV = False, param_grid = None):
    # get zones in data
    zones = np.sort(data_train.ZONEID.unique())

    # initialize DataFrame where predictions of various zones are saved
    y_trainpred, y_testpred = pd.DataFrame(), pd.DataFrame()

    # save scores of linear regression models for different zones in dictionary
    trainscore, testscore, model_dict = {}, {}, {}

    # loop over zones
    for zone in zones:
        model_clone = clone(model)
        # split train and test data in feature and TARGETVAR parts and cut data to desired zones
        X_train, X_test, y_train, y_test = train_test_feat(data_train, data_test, zone, features)

        # scale data if scaler is not None
        if scaler:
            X_train, X_test = scaler_func(X_train, X_test, scaler)

        # train model
        if perform_gridCV:
            if param_grid:
                print(f'ZONEID {zone}')
                cv = GridSearchCV(model_clone, param_grid= param_grid, scoring = 'neg_root_mean_squared_error', refit=True, n_jobs=-1, verbose=2)
                cv.fit(X_train,y_train)
                model_clone = cv
            else:
                raise ValueError('No parameter grid given for Grid Search')
        else:
            model_clone.fit(X_train, y_train)
        

        if save_models:
            model_dict[zone] = deepcopy(model_clone)

        # predict train data with the model_clone and calculate train-score
        y_pred = predict_func(model_clone, X_train, y_train)
        y_trainpred = pd.concat([y_trainpred, y_pred], axis=0)
        trainscore['ZONE' + str(zone)] = mean_squared_error(y_pred, y_train, squared=False)

        # predict test data with the model_clone and calculate test-score
        y_pred = predict_func(model_clone, X_test, y_test)
        y_testpred = pd.concat([y_testpred, y_pred], axis=0)
        testscore['ZONE' + str(zone)] = mean_squared_error(y_pred, y_test, squared=False)

    # merge final train and test predictions with observations to ensure a right order in both data  
    y_trainpred = y_trainpred.join(data_train.TARGETVAR)
    y_trainpred.rename(columns = {'TARGETVAR':'test'}, inplace=True)
    trainscore['TOTAL'] = mean_squared_error(y_trainpred['test'], y_trainpred['pred'], squared=False)


    y_testpred = y_testpred.join(data_test.TARGETVAR)
    y_testpred.rename(columns = {'TARGETVAR':'test'}, inplace=True)
    testscore['TOTAL'] = mean_squared_error(y_testpred['test'], y_testpred['pred'], squared=False)


    # print scores if desired
    if print_scores:
        for key in testscore.keys():
            print(f'train-RMSE/test-RMSE linear regression model for {key}: {round(trainscore[key],3)} {round(testscore[key],3)}')

    # track to MLFLow
    if log:
        for key in testscore.keys():
            log_to_mlflow(ZONEID=key, Model=model.__class__.__name__, features=features, train_RMSE=trainscore[key], test_RMSE=testscore[key], 
                          nan_removed=True, zero_removed=False, mean=None, 
                          hyperparameter=model.get_params(), model_parameters=None, scaler=scaler, info=infotext_mlflow)


    if save_models:
        return trainscore, testscore, model_dict
    else:
        return trainscore, testscore


def scaler_func(X_train, X_test, scaler):
    X_train = scaler.fit_transform(X_train)
    X_test = scaler.transform(X_test)

    if scaler.__class__.__name__ == "MinMaxScaler":
        print(f'Scaler: MinMaxScaler')
        print(f'Scaled X_train min/max: {round(X_train.min(),2)}, {round(X_train.max(),2)}')
        print(f'Scaled X_test min/max: {round(X_test.min(),2)}, {round(X_test.max(),2)}\n')

    if scaler.__class__.__name__ == "StandardScaler":
        print(f'Scaler: StandardScaler')
        print(f'Scaled X_train mean/std: {round(X_train.mean(),2)}, {round(X_train.std(),2)}')
        print(f'Scaled X_test mean/std: {round(X_test.mean(),2)}, {round(X_test.std(),2)}\n')
    
    return X_train, X_test

def train_test_feat(data_train, data_test, zone, features):
    X_train = data_train[data_train.ZONEID == zone][features]
    y_train = data_train[data_train.ZONEID == zone].TARGETVAR

    X_test = data_test[data_test.ZONEID == zone][features]
    y_test = data_test[data_test.ZONEID == zone].TARGETVAR
    return X_train, X_test, y_train, y_test

def predict_func(model, X, y):
    y_pred = model.predict(X)
    y_pred = pd.DataFrame([1 if value >= 1 else 0 if value <= 0 else value for value in y_pred], index = y.index, columns = ['pred'])
    return y_pred
    y_trainpred = pd.concat([y_trainpred, y_pred], axis=0)
    trainscore['ZONE' + str(zone)] = mean_squared_error(y_pred, y_train, squared=False)


def get_features(data):
    features = data.columns.to_list()
    features = [var for var in features if var not in ('ZONEID','TARGETVAR','TIMESTAMP')]

    feature_dict = {}

    feature_dict['all'] = features
    feature_dict['no_deg'] = [var for var in features if var not in ('WD100','WD10')]
    feature_dict['no_deg_norm'] = [var for var in features if var not in ('WD100','WD10','U100NORM','V100NORM')]
    feature_dict['no_deg_norm_U10V10'] = [var for var in features if var not in ('WD100','WD10','U100NORM','V100NORM','U10','V10')]
    feature_dict['no_deg_norm_WS10'] = [var for var in features if var not in ('WD100','WD10','U100NORM','V100NORM','WS10')]

    feature_dict['no_comp'] = [var for var in features if var not in ('U10','U100','U100NORM','V10','V100','V100NORM')]
    feature_dict['no_comp_plus_100Norm'] = [var for var in features if var not in ('U10','U100','V10','V100')]
    feature_dict['no_deg_comp'] = [var for var in features if var in feature_dict['no_deg'] and var in feature_dict['no_comp']]
    feature_dict['no_ten'] = [var for var in features if 'WD10CARD' not in var and var not in ('U10','V10','WS10','WD10')]
    feature_dict['no_card'] = [var for var in features if 'CARD' not in var]
    feature_dict['no_deg_comp_ten'] = [var for var in feature_dict['no_deg_comp'] if var in feature_dict['no_ten']]

    return feature_dict


